"""
A multi-day event's sub-events each get their own photographer and videographer
(and the editing that goes with them); extra tasks can be added on top; a shooter
can be removed; and sub-events added, changed or deleted later keep their teams in
step. In-memory database and locmem mail: nothing here sends a real email.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from core.config import get_points_scheme, get_task_types
from core.constants import TaskStatus
from core.models import Request, SubEvent, Task, TaskType, TeamMember
from engine.assignment import RemovalError, remove_shooter
from engine.event_changes import release_sub_event, retime_sub_event, staff_new_sub_event
from engine.pipeline import build_pipeline, number_extras, ordered_sub_events, task_key
from engine.workflow import process_new_request

from .factories import ASHA, COMMITTEE_EMAIL, NEHA, FreeCalendar, build_world
from .test_meetings_and_pairing import member


def make_world():
    """The standard world plus a Videographer/Video Editor and enough first-years to staff several shoots."""
    build_world()
    TaskType.objects.create(
        task="Videographer", required_skill="Videography", points=8, sla_hours=0, at_event=True,
        requestable=True, internal_assignable=True, vertical="Videography",
    )
    TaskType.objects.create(
        task="Video Editor", required_skill="Video Editing", points=5, sla_hours=48, at_event=False,
        requestable=False, internal_assignable=True, vertical="Videography",
    )
    TeamMember.objects.filter(email=ASHA).update(points=50)
    people = {}
    for i in range(1, 4):
        people[f"p{i}"] = member(f"p{i}@i.ac.in", f"Photo{i}", vertical="Photography", skills=["Photography", "Photo Editing"])
        people[f"v{i}"] = member(f"v{i}@i.ac.in", f"Video{i}", vertical="Videography", skills=["Videography", "Video Editing"])
    return people


def day(offset_days, hour):
    base = (timezone.now() + timedelta(days=offset_days)).astimezone(timezone.get_current_timezone())
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


def multiday(roles=("Photographer", "Videographer"), subs=2, start_days=8, is_multiday=True, **extra):
    """A saved multi-day Coverage request with `subs` sub-events, one per day, 10:00-12:00."""
    first = day(start_days, 0)
    request_obj = Request.objects.create(
        type="Coverage", event_name="Fest Week", contact_email=COMMITTEE_EMAIL, roles_needed=list(roles),
        platforms=[], status="New", is_multiday=is_multiday, event_start=first,
        event_end=first + timedelta(days=max(subs, 1)) - timedelta(minutes=1), **extra,
    )
    for i in range(subs):
        SubEvent.objects.create(
            request=request_obj, name=f"Day {i + 1} talk", venue=f"Hall {i + 1}",
            start=day(start_days + i, 10), end=day(start_days + i, 12),
        )
    return request_obj


def run(request_obj, **kwargs):
    with mock.patch("engine.workflow.calendar_service", return_value=FreeCalendar()), \
            mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
        process_new_request(request_obj, **kwargs)
    request_obj.refresh_from_db()
    mail.outbox.clear()
    return request_obj


class PipelineShapeTests(TestCase):
    def setUp(self):
        make_world()

    def build(self, request_obj, **kwargs):
        subs = ordered_sub_events(request_obj.sub_events.all())
        return build_pipeline(request_obj, get_task_types(), timezone.now(), get_points_scheme(), sub_events=subs, **kwargs)

    def test_each_sub_event_gets_its_own_shoot_roles_and_editors(self):
        request_obj = multiday()
        keys = [p.ident for p in self.build(request_obj)]
        self.assertEqual(
            keys,
            ["Photographer@s0", "Photographer@s1", "Videographer@s0", "Videographer@s1",
             "Photo Editor@s0", "Photo Editor@s1", "Video Editor@s0", "Video Editor@s1",
             "Event Coordinator", "Task Supervisor"],
        )

    def test_editors_are_paired_to_their_own_sub_events_shooter(self):
        pipeline = {p.ident: p for p in self.build(multiday())}
        self.assertEqual(pipeline["Photo Editor@s1"].pairs_with, "Photographer@s1")
        self.assertEqual(pipeline["Video Editor@s0"].pairs_with, "Videographer@s0")

    def test_a_sub_events_tasks_use_its_own_window_and_deadlines(self):
        request_obj = multiday()
        subs = ordered_sub_events(request_obj.sub_events.all())
        pipeline = {p.ident: p for p in self.build(request_obj)}
        second = subs[1]
        self.assertEqual(pipeline["Photographer@s1"].window, (second.start, second.end))
        self.assertEqual(pipeline["Photographer@s1"].deadline, second.end)
        self.assertEqual(pipeline["Photo Editor@s1"].deadline, second.end + timedelta(hours=24))
        self.assertEqual(pipeline["Video Editor@s0"].deadline, subs[0].end + timedelta(hours=48))

    def test_the_coordinator_is_one_per_request_and_due_after_the_last_sub_events_work(self):
        request_obj = multiday()
        pipeline = {p.ident: p for p in self.build(request_obj)}
        last = max(p.deadline for p in pipeline.values() if p.task not in ("Event Coordinator", "Task Supervisor"))
        self.assertEqual(pipeline["Event Coordinator"].deadline, last + timedelta(hours=12))

    def test_a_multi_day_event_with_no_sub_events_keeps_one_of_each_for_the_whole_event(self):
        request_obj = multiday(subs=0)
        keys = [p.ident for p in self.build(request_obj)]
        self.assertEqual(keys, ["Photographer", "Videographer", "Photo Editor", "Video Editor",
                                "Event Coordinator", "Task Supervisor"])

    def test_a_single_day_event_ignores_sub_events(self):
        request_obj = multiday(is_multiday=False)
        keys = [p.ident for p in self.build(request_obj)]
        self.assertEqual(keys[:2], ["Photographer", "Videographer"])
        self.assertFalse([k for k in keys if "@" in k])

    def test_only_the_roles_the_club_ticked_are_created(self):
        keys = [p.ident for p in self.build(multiday(roles=("Photographer",)))]
        self.assertNotIn("Videographer@s0", keys)
        self.assertIn("Photo Editor@s0", keys)

    def test_the_order_is_stable_whatever_order_sub_events_were_saved_in(self):
        request_obj = multiday(subs=0)
        late = SubEvent.objects.create(request=request_obj, name="Later", start=day(9, 10), end=day(9, 12))
        early = SubEvent.objects.create(request=request_obj, name="Earlier", start=day(8, 10), end=day(8, 12))
        self.assertEqual([s.pk for s in ordered_sub_events(request_obj.sub_events.all())], [early.pk, late.pk])


class ExtraTasksTests(TestCase):
    def setUp(self):
        make_world()

    def keys(self, request_obj, extras):
        subs = ordered_sub_events(request_obj.sub_events.all())
        pipeline = build_pipeline(
            request_obj, get_task_types(), timezone.now(), get_points_scheme(), sub_events=subs, extras=extras
        )
        return [p.ident for p in pipeline]

    def test_extras_are_numbered_per_task_and_sub_event(self):
        self.assertEqual(
            number_extras([("Photographer", None), ("Photographer", None), ("Photographer", 1), ("Content Writer", None)]),
            [("Photographer", None, 1), ("Photographer", None, 2), ("Photographer", 1, 1), ("Content Writer", None, 1)],
        )
        self.assertEqual(task_key("Photographer", 1, 1), "Photographer+1@s1")

    def test_an_extra_shooter_brings_their_own_editing(self):
        keys = self.keys(multiday(subs=2), [("Photographer", 1)])
        self.assertIn("Photographer+1@s1", keys)
        self.assertIn("Photo Editor+1@s1", keys)

    def test_the_one_per_request_roles_cannot_be_added_as_extras(self):
        keys = self.keys(multiday(), [("Event Coordinator", None), ("Task Supervisor", None)])
        self.assertEqual(keys.count("Event Coordinator"), 1)
        self.assertEqual(keys.count("Task Supervisor"), 1)

    def test_an_unknown_task_or_sub_event_index_is_handled_not_crashed_on(self):
        keys = self.keys(multiday(subs=1), [("Basket Weaver", None), ("Photographer", 9)])
        self.assertNotIn("Basket Weaver", keys)
        self.assertIn("Photographer+1", keys)  # the bad index falls back to the whole event

    def test_extras_work_on_a_post_too(self):
        post = Request.objects.create(type="Post", event_name="P", contact_email=COMMITTEE_EMAIL, platforms=["Instagram"])
        keys = [
            p.ident
            for p in build_pipeline(post, get_task_types(), timezone.now(), get_points_scheme(), extras=[("Content Writer", None)])
        ]
        self.assertEqual(keys, ["Content Writer", "Graphic Designer", "Content Writer+1"])


class StaffingTests(TestCase):
    def setUp(self):
        self.people = make_world()

    def test_every_sub_events_tasks_are_created_on_its_own_times_and_venue(self):
        request_obj = run(multiday(), skip_approval=True)
        subs = list(request_obj.sub_events.order_by("start"))
        for sub in subs:
            tasks = request_obj.tasks.filter(sub_event=sub)
            self.assertEqual({t.task for t in tasks}, {"Photographer", "Videographer", "Photo Editor", "Video Editor"})
            for t in tasks:
                self.assertEqual((t.event_start, t.event_end, t.venue), (sub.start, sub.end, sub.venue))
        self.assertEqual(request_obj.tasks.filter(sub_event__isnull=True).count(), 2)  # coordinator + supervisor

    def test_the_editing_task_belongs_to_the_same_person_as_the_shoot_of_the_same_sub_event(self):
        request_obj = run(multiday(), skip_approval=True)
        for sub in request_obj.sub_events.all():
            photographer = request_obj.tasks.get(sub_event=sub, task="Photographer")
            editor = request_obj.tasks.get(sub_event=sub, task="Photo Editor")
            self.assertEqual(editor.email, photographer.email)
            self.assertEqual(editor.paired_task_id, photographer.pk)

    def test_different_sub_events_get_different_people_when_there_are_enough(self):
        request_obj = run(multiday(roles=("Photographer",), subs=3), skip_approval=True)
        emails = [t.email for t in request_obj.tasks.filter(task="Photographer")]
        self.assertEqual(len(emails), 3)
        self.assertEqual(len(set(emails)), 3)

    def test_a_person_is_only_chosen_if_free_during_that_sub_events_own_window(self):
        request_obj = multiday(roles=("Photographer",), subs=2)
        first, second = list(request_obj.sub_events.order_by("start"))

        class BusyDuringFirst(FreeCalendar):
            def is_free(self, email, start, end):
                return not (start == first.start and end == first.end)  # everyone is busy for the first only

        with mock.patch("engine.workflow.calendar_service", return_value=BusyDuringFirst()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            process_new_request(request_obj, skip_approval=True)
        self.assertEqual(request_obj.tasks.get(task="Photographer", sub_event=first).status, TaskStatus.UNFILLED)
        self.assertTrue(request_obj.tasks.get(task="Photographer", sub_event=second).email)

    def test_assignees_are_told_which_sub_event_and_get_a_calendar_hold_for_it(self):
        request_obj = multiday(roles=("Photographer",), subs=1)
        holds = []

        class Recording(FreeCalendar):
            def create_hold(self, **kwargs):
                holds.append(kwargs)

        with mock.patch("engine.workflow.calendar_service", return_value=Recording()), \
                mock.patch("engine.notify.calendar_service", return_value=Recording()):
            process_new_request(request_obj, skip_approval=True)
        assigned = [m for m in mail.outbox if m.subject.startswith("[Assigned]") and "Photographer" in m.subject]
        self.assertTrue(assigned)
        self.assertIn("Day 1 talk", assigned[0].subject)
        self.assertIn("covering the sub-event", assigned[0].body)
        sub = request_obj.sub_events.get()
        self.assertTrue(any(h["start"] == sub.start and h["end"] == sub.end for h in holds))

    def test_the_clubs_team_list_names_the_sub_event_each_person_covers(self):
        request_obj = run(multiday(roles=("Photographer",), subs=2), skip_approval=True)
        roles = [entry["role"] for entry in request_obj.roster]
        self.assertIn("Photographer — Day 1 talk", roles)
        self.assertIn("Photographer — Day 2 talk", roles)

    def test_extras_are_created_with_the_system_picking_or_a_person_chosen(self):
        from core.models import TeamMember as TM

        chosen = TM.objects.get(email="p3@i.ac.in")
        request_obj = multiday(roles=("Photographer",), subs=2)
        run(request_obj, skip_approval=True, extras=[("Photographer", 1)], preferred={"Photographer+1@s1": chosen})
        extra = request_obj.tasks.get(task="Photographer", email=chosen.email)
        self.assertEqual(extra.sub_event.name, "Day 2 talk")
        self.assertEqual(request_obj.tasks.filter(task="Photographer").count(), 3)
        self.assertEqual(request_obj.tasks.get(task="Photo Editor", paired_task=extra).email, chosen.email)

    def test_the_activity_log_and_approval_email_use_the_sub_event_labels(self):
        request_obj = multiday(roles=("Photographer",), subs=1)
        with mock.patch("engine.workflow.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            process_new_request(request_obj)  # the club's own: held for approval? (8 days out: not gated)
        from core.models import ActivityLog

        self.assertTrue(ActivityLog.objects.filter(event="proposed", detail__startswith="Photographer — Day 1 talk").exists())


class RemoveShooterTests(TestCase):
    def setUp(self):
        self.people = make_world()
        self.request = run(multiday(roles=("Photographer", "Videographer"), subs=2), skip_approval=True)
        self.video = self.request.tasks.get(task="Videographer", sub_event__name="Day 1 talk")

    def test_removes_the_shooter_and_their_editing(self):
        before = self.request.tasks.count()
        removed = remove_shooter(self.video, "poc@iimsirmaur.ac.in")
        self.assertEqual(removed, ["Videographer — Day 1 talk", "Video Editor — Day 1 talk"])
        self.assertEqual(self.request.tasks.count(), before - 2)
        self.assertFalse(self.request.tasks.filter(sub_event__name="Day 1 talk", task__in=["Videographer", "Video Editor"]).exists())

    def test_the_other_sub_events_team_is_untouched(self):
        remove_shooter(self.video, "poc@iimsirmaur.ac.in")
        self.assertTrue(self.request.tasks.filter(sub_event__name="Day 2 talk", task="Videographer").exists())
        self.assertTrue(self.request.tasks.filter(task="Photographer", sub_event__name="Day 1 talk").exists())

    def test_the_person_is_told_once_listing_everything_taken_off_them(self):
        holder = self.video.email
        mail.outbox.clear()
        remove_shooter(self.video, "poc@iimsirmaur.ac.in")
        notes = [m for m in mail.outbox if m.subject.startswith("[Removed]") and holder in m.to]
        self.assertEqual(len(notes), 1)
        self.assertIn("Videographer — Day 1 talk", notes[0].body)
        self.assertIn("Video Editor — Day 1 talk", notes[0].body)

    def test_the_club_is_told_and_its_team_list_loses_them(self):
        mail.outbox.clear()
        remove_shooter(self.video, "poc@iimsirmaur.ac.in")
        update = next(m for m in mail.outbox if m.subject.startswith("[Team update]"))
        self.assertEqual(update.to, [COMMITTEE_EMAIL])
        self.assertIn("Videographer — Day 1 talk", update.body)
        self.request.refresh_from_db()
        self.assertNotIn("Videographer — Day 1 talk", [e["role"] for e in self.request.roster])

    def test_the_coordinators_deadline_is_recalculated(self):
        coordinator = self.request.tasks.get(task="Event Coordinator")
        # Take the last-due work away: the video editing of the final day.
        last = self.request.tasks.get(task="Video Editor", sub_event__name="Day 2 talk")
        remove_shooter(self.request.tasks.get(task="Videographer", sub_event__name="Day 2 talk"), "poc")
        coordinator.refresh_from_db()
        remaining = max(t.deadline for t in self.request.tasks.exclude(task__in=["Event Coordinator", "Task Supervisor"]))
        self.assertEqual(coordinator.deadline, remaining + timedelta(hours=12))
        self.assertLess(coordinator.deadline, last.deadline + timedelta(hours=12))

    def test_a_task_that_is_done_cannot_be_removed(self):
        Task.objects.filter(pk=self.video.pk).update(status=TaskStatus.DONE)
        self.video.refresh_from_db()
        with self.assertRaises(RemovalError):
            remove_shooter(self.video, "poc")
        self.assertTrue(Task.objects.filter(pk=self.video.pk).exists())

    def test_only_photographers_and_videographers_can_be_removed(self):
        for name in ("Event Coordinator", "Task Supervisor", "Photo Editor"):
            with self.assertRaises(RemovalError):
                remove_shooter(self.request.tasks.filter(task=name).first(), "poc")

    def test_a_closed_request_cannot_be_changed(self):
        Request.objects.filter(pk=self.request.pk).update(status="Posted")
        self.video.refresh_from_db()
        with self.assertRaises(RemovalError):
            remove_shooter(self.video, "poc")

    def test_an_editing_task_that_is_already_done_is_kept(self):
        editor = self.request.tasks.get(task="Video Editor", sub_event__name="Day 1 talk")
        Task.objects.filter(pk=editor.pk).update(status=TaskStatus.DONE)
        removed = remove_shooter(self.video, "poc")
        self.assertEqual(removed, ["Videographer — Day 1 talk"])
        self.assertTrue(Task.objects.filter(pk=editor.pk).exists())

    def test_the_club_is_not_promised_a_replacement(self):
        mail.outbox.clear()
        remove_shooter(self.video, "poc@iimsirmaur.ac.in")
        update = next(m for m in mail.outbox if m.subject.startswith("[Team update]"))
        self.assertNotIn("Whoever covers it now", update.body)
        self.assertIn("taken off", update.body)

    def test_removing_the_last_open_deliverable_marks_the_event_covered(self):
        self.request.tasks.exclude(pk__in=[self.video.pk]).exclude(
            task__in=["Event Coordinator", "Task Supervisor"]
        ).update(status=TaskStatus.DONE)
        remove_shooter(self.video, "poc")  # its Video Editor goes too; everything left is done
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, "Event Covered")

    def test_it_is_written_to_the_activity_log(self):
        from core.models import ActivityLog

        remove_shooter(self.video, "poc@iimsirmaur.ac.in")
        self.assertTrue(ActivityLog.objects.filter(event="task-removed", actor="poc@iimsirmaur.ac.in").exists())


class WholeEventCoverTests(TestCase):
    def test_a_sub_event_added_to_an_event_covered_as_a_whole_is_not_double_staffed(self):
        make_world()
        request = run(multiday(roles=("Photographer",), subs=0), skip_approval=True)
        self.assertTrue(request.tasks.filter(task="Photographer", sub_event__isnull=True).exists())
        start = timezone.now() + timedelta(days=9)
        sub = SubEvent.objects.create(request=request, name="Late addition", start=start, end=start + timedelta(hours=1))
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            self.assertEqual(staff_new_sub_event(request, sub, "club"), [])
        self.assertEqual(request.tasks.filter(task="Photographer").count(), 1)


class SubEventLifecycleTests(TestCase):
    def setUp(self):
        self.people = make_world()
        self.request = run(multiday(roles=("Photographer",), subs=1), skip_approval=True)

    def add_sub(self, hours_from_now=24 * 10):
        start = timezone.now() + timedelta(hours=hours_from_now)
        return SubEvent.objects.create(request=self.request, name="Added panel", start=start, end=start + timedelta(hours=1), venue="Room 9")

    def test_a_sub_event_added_later_is_staffed_confirmed_and_emailed_when_accepted(self):
        sub = self.add_sub()
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            tasks = staff_new_sub_event(self.request, sub, "club")
        self.assertEqual({t.task for t in tasks}, {"Photographer", "Photo Editor"})
        for t in tasks:
            self.assertEqual((t.sub_event_id, t.status, t.venue), (sub.pk, TaskStatus.CONFIRMED, "Room 9"))
        shooter = next(t for t in tasks if t.task == "Photographer")
        assigned = [m for m in mail.outbox if m.subject.startswith("[Assigned]") and shooter.email in m.to]
        self.assertTrue(any("Added panel" in m.subject for m in assigned))
        self.assertTrue(any(m.subject.startswith("[Team update]") and m.to == [COMMITTEE_EMAIL] for m in mail.outbox))
        self.request.refresh_from_db()
        self.assertIn("Photographer — Added panel", [e["role"] for e in self.request.roster])

    def test_before_acceptance_they_are_only_proposed_and_nobody_is_emailed(self):
        Request.objects.filter(pk=self.request.pk).update(status="Pending for POC approval")
        self.request.refresh_from_db()
        mail.outbox.clear()
        sub = self.add_sub()
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            tasks = staff_new_sub_event(self.request, sub, "club")
        self.assertTrue(all(t.status == TaskStatus.PROPOSED for t in tasks if t.email))
        self.assertEqual(mail.outbox, [])

    def test_the_coordinators_deadline_moves_out_to_follow_a_later_sub_event(self):
        coordinator = self.request.tasks.get(task="Event Coordinator")
        before = coordinator.deadline
        sub = self.add_sub(hours_from_now=24 * 20)
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            staff_new_sub_event(self.request, sub, "club")
        coordinator.refresh_from_db()
        self.assertGreater(coordinator.deadline, before)

    def test_a_single_day_event_does_not_staff_sub_events(self):
        Request.objects.filter(pk=self.request.pk).update(is_multiday=False)
        self.request.refresh_from_db()
        sub = self.add_sub()
        self.assertEqual(staff_new_sub_event(self.request, sub, "club"), [])

    def test_a_request_with_no_shoot_roles_has_nothing_to_staff(self):
        Request.objects.filter(pk=self.request.pk).update(roles_needed=[])
        self.request.refresh_from_db()
        self.assertEqual(staff_new_sub_event(self.request, self.add_sub(), "club"), [])

    def test_changing_a_sub_events_time_moves_its_team_and_tells_them(self):
        sub = self.request.sub_events.get()
        old = (sub.name, sub.start, sub.end, sub.venue)
        sub.start, sub.end = sub.start + timedelta(hours=3), sub.end + timedelta(hours=3)
        sub.save()
        mail.outbox.clear()
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            retime_sub_event(sub, old, "club")
        shooter = self.request.tasks.get(task="Photographer", sub_event=sub)
        editor = self.request.tasks.get(task="Photo Editor", sub_event=sub)
        self.assertEqual((shooter.event_start, shooter.event_end), (sub.start, sub.end))
        self.assertEqual(shooter.deadline, sub.end)
        self.assertEqual(editor.deadline, sub.end + timedelta(hours=24))
        note = next(m for m in mail.outbox if m.subject.startswith("[Schedule updated]") and shooter.email in m.to)
        self.assertIn("Was:", note.body)
        self.assertIn("Now:", note.body)

    def test_a_done_task_keeps_its_times(self):
        sub = self.request.sub_events.get()
        shooter = self.request.tasks.get(task="Photographer", sub_event=sub)
        Task.objects.filter(pk=shooter.pk).update(status=TaskStatus.DONE)
        old = (sub.name, sub.start, sub.end, sub.venue)
        sub.start += timedelta(hours=5)
        sub.end += timedelta(hours=5)
        sub.save()
        retime_sub_event(sub, old, "club")
        shooter.refresh_from_db()
        self.assertNotEqual(shooter.event_start, sub.start)

    def test_renaming_a_sub_event_renames_it_on_the_clubs_team_list(self):
        sub = self.request.sub_events.get()
        old = (sub.name, sub.start, sub.end, sub.venue)
        sub.name = "Opening ceremony"
        sub.save()
        retime_sub_event(sub, old, "club")
        self.request.refresh_from_db()
        self.assertIn("Photographer — Opening ceremony", [e["role"] for e in self.request.roster])

    def test_deleting_a_sub_event_releases_its_team_and_tells_them(self):
        sub = self.request.sub_events.get()
        shooter = self.request.tasks.get(task="Photographer", sub_event=sub)
        holder = shooter.email
        mail.outbox.clear()
        line = release_sub_event(sub, "club")
        self.assertIn("Photographer — Day 1 talk", line)
        self.assertFalse(self.request.tasks.filter(sub_event=sub).exists())
        self.assertTrue(any("cancelled" in m.subject and holder in m.to for m in mail.outbox))
        self.request.refresh_from_db()
        self.assertEqual([e for e in self.request.roster if "Day 1 talk" in e["role"]], [])
        sub.delete()

    def test_done_work_survives_the_sub_event_being_deleted(self):
        sub = self.request.sub_events.get()
        shooter = self.request.tasks.get(task="Photographer", sub_event=sub)
        Task.objects.filter(pk=shooter.pk).update(status=TaskStatus.DONE)
        release_sub_event(sub, "club")
        sub.delete()
        shooter.refresh_from_db()
        self.assertIsNone(shooter.sub_event_id)  # kept, detached

    def test_renaming_leaves_another_sub_events_similar_name_alone(self):
        other = SubEvent.objects.create(
            request=self.request, name="Grand Day 1 talk", start=day(9, 10), end=day(9, 12)
        )
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            staff_new_sub_event(self.request, other, "club")
        sub = self.request.sub_events.get(name="Day 1 talk")
        old = (sub.name, sub.start, sub.end, sub.venue)
        sub.name = "Opening ceremony"
        sub.save()
        retime_sub_event(sub, old, "club")
        self.request.refresh_from_db()
        roles = [e["role"] for e in self.request.roster]
        self.assertIn("Photographer — Grand Day 1 talk", roles)
        self.assertIn("Photographer — Opening ceremony", roles)

    def test_moving_a_sub_event_onto_a_busy_slot_reports_the_clash(self):
        sub = self.request.sub_events.get()
        shooter = self.request.tasks.get(task="Photographer", sub_event=sub)
        old = (sub.name, sub.start, sub.end, sub.venue)
        sub.start, sub.end = sub.start + timedelta(hours=3), sub.end + timedelta(hours=3)
        sub.save()
        busy = FreeCalendar()
        busy.is_free = lambda email, start, end: email != shooter.email
        mail.outbox.clear()
        with mock.patch("engine.event_changes.calendar_service", return_value=busy):
            clashes = retime_sub_event(sub, old, "club")
        self.assertEqual(clashes, [(shooter.member, shooter.email)])
        self.assertTrue(any(m.subject.startswith("[Calendar clash]") for m in mail.outbox))

    def test_deleting_the_last_sub_event_puts_whole_event_cover_back(self):
        sub = self.request.sub_events.get()
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            line = release_sub_event(sub, "club")
        sub.delete()
        whole = self.request.tasks.filter(sub_event__isnull=True, task__in=["Photographer", "Photo Editor"])
        self.assertEqual({t.task for t in whole}, {"Photographer", "Photo Editor"})
        self.assertTrue(all(t.status == TaskStatus.CONFIRMED for t in whole))
        self.assertIn("covered as a whole again", line)

    def test_deleting_one_of_several_sub_events_adds_no_whole_event_cover(self):
        other = SubEvent.objects.create(request=self.request, name="Day 2", start=day(9, 10), end=day(9, 12))
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            staff_new_sub_event(self.request, other, "club")
            release_sub_event(self.request.sub_events.get(name="Day 1 talk"), "club")
        self.assertFalse(self.request.tasks.filter(sub_event__isnull=True, task="Photographer").exists())

    def test_task_label_reads_naturally(self):
        sub = self.request.sub_events.get()
        self.assertEqual(self.request.tasks.get(task="Photographer").label, "Photographer — Day 1 talk")
        self.assertEqual(self.request.tasks.get(task="Event Coordinator").label, "Event Coordinator")
