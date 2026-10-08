"""
Views for per-sub-event teams: the team page (sub-event groups and "Add a task"),
adding and removing a photographer/videographer from Assignments, and the club's
sub-event add/edit/delete keeping teams in step.

Django's test client + locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import Availability, TaskStatus
from core.models import Committee, Request, SubEvent, Task, TaskType, TeamMember
from engine.tests.factories import ASHA, COMMITTEE_EMAIL, FreeCalendar, build_world
from engine.tests.test_meetings_and_pairing import member

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"


def fmt(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


def sub_rows(*rows):
    data = {"sub-TOTAL_FORMS": str(max(1, len(rows))), "sub-INITIAL_FORMS": "0",
            "sub-MIN_NUM_FORMS": "0", "sub-MAX_NUM_FORMS": "1000"}
    for i in range(max(1, len(rows))):
        row = rows[i] if i < len(rows) else {}
        for key in ("name", "start", "end", "venue", "notes"):
            data[f"sub-{i}-{key}"] = row.get(key, "")
    return data


def at(offset_days, hour):
    base = (timezone.now() + timedelta(days=offset_days)).astimezone(timezone.get_current_timezone())
    return base.replace(hour=hour, minute=0, second=0, microsecond=0)


class Base(TestCase):
    def setUp(self):
        build_world()
        for name, skill, vertical in (("Videographer", "Videography", "Videography"), ("Video Editor", "Video Editing", "Videography")):
            TaskType.objects.create(
                task=name, required_skill=skill, points=5, sla_hours=0 if name == "Videographer" else 48,
                at_event=name == "Videographer", requestable=name == "Videographer",
                internal_assignable=True, vertical=vertical,
            )
        TeamMember.objects.filter(email=ASHA).update(points=80)
        self.ravi = member("ravi@iimsirmaur.ac.in", "Ravi", vertical="Photography", skills=["Photography", "Photo Editing"])
        self.vic = member("vic@iimsirmaur.ac.in", "Vic", vertical="Videography", skills=["Videography", "Video Editing"])
        self.pia = member("pia@iimsirmaur.ac.in", "Pia", vertical="Photography", skills=["Photography", "Photo Editing"])
        self.sue = member("sue@iimsirmaur.ac.in", "Sue", year=2)
        self.oli = member("oli@iimsirmaur.ac.in", "Oli", availability=Availability.OUT)
        self.committee = Committee.objects.get(email=COMMITTEE_EMAIL)
        self.poc = User.objects.create_user("poc", email=POC)
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.url = f"{reverse('request-new')}?for={self.committee.pk}"
        self.day1, self.day2 = at(9, 10), at(10, 14)
        mail.outbox.clear()

    def body(self, roles=("Photographer",), **extra):
        first = timezone.localtime(self.day1).date()
        body = {
            "type": "Coverage", "event_name": "Fest Week", "event_kind": "multi",
            "start_date": first.isoformat(), "end_date": (first + timedelta(days=2)).isoformat(),
            "roles_needed": list(roles), "platforms": ["Instagram"],
            **sub_rows(
                {"name": "Opening", "start": fmt(self.day1), "end": fmt(self.day1 + timedelta(hours=2)), "venue": "Hall A"},
                {"name": "Closing", "start": fmt(self.day2), "end": fmt(self.day2 + timedelta(hours=2)), "venue": "Hall B"},
            ),
        }
        body.update(extra)
        return body

    def post(self, step="", picks=None, extras=(), **extra):
        data = {**self.body(**extra), "step": step} if step else self.body(**extra)
        data.update({f"pick:{k}": v for k, v in (picks or {}).items()})
        if extras:
            data["extra-TOTAL"] = str(len(extras))
            for i, (task, sub, who) in enumerate(extras):
                data[f"extra-{i}-task"], data[f"extra-{i}-sub"], data[f"extra-{i}-who"] = task, sub, who
        self.client.force_login(self.poc)
        return self.client.post(self.url, data)

    def saved(self):
        return Request.objects.get(event_name="Fest Week")


class TeamPageTests(Base):
    def test_tasks_are_grouped_by_sub_event_with_the_whole_event_roles_last(self):
        response = self.post()
        titles = [g.title for g in response.context["groups"]]
        self.assertEqual(titles, ["Opening", "Closing", "Whole event"])
        keys = [r.key for g in response.context["groups"] for r in g.rows]
        self.assertEqual(keys, ["Photographer@s0", "Photo Editor@s0", "Photographer@s1", "Photo Editor@s1",
                                "Event Coordinator", "Task Supervisor"])

    def test_each_sub_event_group_shows_its_time_and_venue(self):
        group = self.post().context["groups"][0]
        self.assertIn("Hall A", group.detail)
        self.assertIn("10:00", group.detail)

    def test_a_single_day_event_shows_one_unlabelled_group(self):
        self.client.force_login(self.poc)
        start = timezone.now() + timedelta(days=5)
        response = self.client.post(self.url, {
            "type": "Coverage", "event_name": "One day", "event_kind": "single",
            "event_start": fmt(start), "event_end": fmt(start + timedelta(hours=2)),
            "venue": "Hall", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
        })
        self.assertEqual([g.title for g in response.context["groups"]], [""])

    def test_the_add_a_task_area_offers_the_plus_button_the_task_types_and_the_sub_events(self):
        page = self.post()
        self.assertContains(page, 'id="extra-template"')
        # A "+" under each sub-event, and one for the tasks that cover the whole event.
        self.assertContains(page, 'data-sub="0">+ Add a task to Opening')
        self.assertContains(page, 'data-sub="1">+ Add a task to Closing')
        self.assertContains(page, 'data-sub="">+ Add a task to Whole event')
        self.assertIn("Photographer", page.context["extra_tasks"])
        self.assertIn("Content Writer", page.context["extra_tasks"])
        self.assertNotIn("Event Coordinator", page.context["extra_tasks"])
        self.assertNotIn("Task Supervisor", page.context["extra_tasks"])
        self.assertEqual(page.context["extra_subs"], [("0", "Opening"), ("1", "Closing")])
        self.assertNotIn(self.sue.email, {e for e, _ in page.context["extra_people"]})
        self.assertNotIn(self.oli.email, {e for e, _ in page.context["extra_people"]})

    def test_a_single_day_event_has_no_sub_event_choice_on_extra_rows(self):
        self.client.force_login(self.poc)
        start = timezone.now() + timedelta(days=5)
        page = self.client.post(self.url, {
            "type": "Coverage", "event_name": "One day", "event_kind": "single",
            "event_start": fmt(start), "event_end": fmt(start + timedelta(hours=2)),
            "venue": "Hall", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
        })
        self.assertEqual(page.context["extra_subs"], [])
        self.assertNotContains(page, 'name="extra-__prefix__-sub"')

    def test_nothing_is_saved_by_previewing_a_multi_day_team(self):
        self.post()
        self.assertEqual((Request.objects.count(), SubEvent.objects.count(), Task.objects.count()), (0, 0, 0))
        self.assertEqual(mail.outbox, [])

    def test_the_extra_rows_are_not_carried_as_plain_hidden_fields(self):
        response = self.post(extras=[("Photographer", "1", self.vic.email)])
        names = {key for key, _ in response.context["carried"]}
        self.assertFalse([n for n in names if n.startswith(("extra-", "pick:"))])
        self.assertIn("sub-0-name", names)  # but the sub-events are


class PicksPerSubEventTests(Base):
    def test_a_different_person_can_be_chosen_for_each_sub_event(self):
        self.post(step="confirm", picks={"Photographer@s0": self.ravi.email, "Photographer@s1": self.vic.email})
        request_obj = self.saved()
        opening = request_obj.tasks.get(task="Photographer", sub_event__name="Opening")
        closing = request_obj.tasks.get(task="Photographer", sub_event__name="Closing")
        self.assertEqual((opening.email, closing.email), (self.ravi.email, self.vic.email))
        # and their editing follows each of them
        self.assertEqual(request_obj.tasks.get(task="Photo Editor", sub_event__name="Opening").email, self.ravi.email)
        self.assertEqual(request_obj.tasks.get(task="Photo Editor", sub_event__name="Closing").email, self.vic.email)

    def test_each_sub_event_task_takes_its_own_time_and_venue_when_saved(self):
        self.post(step="confirm")
        task = self.saved().tasks.get(task="Photographer", sub_event__name="Closing")
        self.assertEqual(task.venue, "Hall B")
        self.assertEqual(task.event_start, self.day2.replace(second=0, microsecond=0))

    def test_a_person_busy_during_one_sub_event_is_refused_for_it_but_fine_for_the_other(self):
        sub_start = self.day1

        class BusyDuringOpening(FreeCalendar):
            def is_free(self, email, start, end):
                return start != sub_start.replace(second=0, microsecond=0)

        with mock.patch("engine.assignment.calendar_service", return_value=BusyDuringOpening()):
            refused = self.post(step="confirm", picks={"Photographer@s0": self.ravi.email})
            fine = self.post(step="confirm", picks={"Photographer@s1": self.ravi.email})
        self.assertContains(refused, "busy during the event window")
        self.assertEqual(Request.objects.count(), 1)  # only the second went through
        self.assertEqual(self.saved().tasks.get(task="Photographer", sub_event__name="Closing").email, self.ravi.email)
        self.assertEqual(fine.status_code, 302)

    def test_a_pick_for_a_role_in_the_wrong_year_is_refused_per_row(self):
        response = self.post(step="confirm", picks={"Photographer@s1": self.sue.email})
        self.assertContains(response, "second-year")
        self.assertEqual(Request.objects.count(), 0)
        rows = {r.key: r for r in response.context["rows"]}
        self.assertIn("second-year", rows["Photographer@s1"].error)
        self.assertEqual(rows["Photographer@s0"].error, "")


class ExtraTasksUITests(Base):
    def test_an_extra_photographer_for_one_sub_event_is_created_with_their_editing(self):
        self.post(step="confirm", extras=[("Photographer", "1", self.vic.email)])
        request_obj = self.saved()
        extra = request_obj.tasks.get(task="Photographer", email=self.vic.email)
        self.assertEqual(extra.sub_event.name, "Closing")
        self.assertEqual(request_obj.tasks.filter(task="Photographer", sub_event__name="Closing").count(), 2)
        self.assertEqual(request_obj.tasks.get(task="Photo Editor", paired_task=extra).email, self.vic.email)

    def test_an_extra_for_the_whole_event_has_no_sub_event(self):
        self.post(step="confirm", extras=[("Photographer", "", self.vic.email)])
        extra = self.saved().tasks.get(task="Photographer", email=self.vic.email)
        self.assertIsNone(extra.sub_event_id)

    def test_any_assignable_task_can_be_added_not_only_shooters(self):
        self.post(step="confirm", extras=[("Content Writer", "", self.ravi.email), ("Graphic Designer", "", "")])
        tasks = self.saved().tasks
        self.assertEqual(tasks.get(task="Content Writer").email, self.ravi.email)
        self.assertTrue(tasks.get(task="Graphic Designer").email)  # the system picked

    def test_several_extras_of_the_same_kind_get_separate_people(self):
        self.post(step="confirm", extras=[("Photographer", "0", self.ravi.email), ("Photographer", "0", self.vic.email)])
        opening = self.saved().tasks.filter(task="Photographer", sub_event__name="Opening")
        self.assertEqual(set(opening.values_list("email", flat=True)) >= {self.ravi.email, self.vic.email}, True)
        self.assertEqual(opening.count(), 3)  # the system's own pick plus the two extras

    def test_a_blank_extra_row_is_ignored(self):
        self.post(step="confirm", extras=[("", "", ""), ("Photographer", "1", self.vic.email)])
        self.assertEqual(self.saved().tasks.filter(task="Photographer").count(), 3)

    def test_the_one_per_request_roles_cannot_be_added(self):
        response = self.post(step="confirm", extras=[("Event Coordinator", "", self.ravi.email)])
        self.assertEqual(Request.objects.count(), 0)
        self.assertContains(response, "can&#x27;t be added here")

    def test_a_person_who_cant_do_it_is_refused_next_to_that_row_and_the_row_is_kept(self):
        response = self.post(step="confirm", extras=[("Photographer", "1", self.oli.email)])
        self.assertEqual(Request.objects.count(), 0)
        self.assertContains(response, "not eligible")
        self.assertEqual([(r.task, r.sub, r.who) for r in response.context["extra_rows"]],
                         [("Photographer", "1", self.oli.email)])
        self.assertIn("not eligible", response.context["extra_rows"][0].error)

    def test_extras_survive_a_round_trip_on_the_team_page(self):
        response = self.post(extras=[("Photographer", "1", self.vic.email)])  # preview with one added
        self.assertEqual(len(response.context["extra_rows"]), 1)
        self.assertContains(response, 'name="extra-0-task"')

    def test_back_to_the_form_keeps_the_sub_events(self):
        response = self.post(step="edit")
        self.assertContains(response, 'value="Opening"')
        self.assertContains(response, 'value="Closing"')
        self.assertEqual(Request.objects.count(), 0)

    def test_a_clubs_own_request_cannot_smuggle_extra_fields(self):
        self.client.force_login(self.club)
        data = {**self.body(), "extra-TOTAL": "1", "extra-0-task": "Photographer", "extra-0-who": self.vic.email,
                f"pick:Photographer@s0": self.vic.email}
        self.client.post(reverse("request-new"), data)
        request_obj = self.saved()
        self.assertEqual(request_obj.created_on_behalf_by, "")
        self.assertEqual(request_obj.tasks.filter(task="Photographer").count(), 2)  # one per sub-event, no extra


class DeleteAndPlusTests(Base):
    """Delete / Restore on a proposed task, and the "+" under each sub-event."""

    def keys(self, response):
        return [r.key for r in response.context["rows"]]

    def test_every_task_but_the_coordinator_and_supervisor_has_a_delete_button(self):
        page = self.post()
        for key in ("Photographer@s0", "Photo Editor@s0", "Photographer@s1", "Photo Editor@s1"):
            self.assertContains(page, f'name="drop" value="{key}"')
        for key in ("Event Coordinator", "Task Supervisor"):
            self.assertNotContains(page, f'name="drop" value="{key}"')

    def test_deleting_a_shooter_takes_their_editing_and_lists_it_for_restoring(self):
        response = self.post(drop="Photographer@s0")
        self.assertEqual(self.keys(response), ["Photographer@s1", "Photo Editor@s1", "Event Coordinator", "Task Supervisor"])
        self.assertContains(response, 'name="restore" value="Photographer@s0"')
        self.assertContains(response, 'name="dropped" value="Photographer@s0"')
        deleted = [r for g in response.context["groups"] for r in g.rows if r.deleted]
        self.assertEqual([(r.key, r.deleted_with_editing) for r in deleted], [("Photographer@s0", True)])
        self.assertEqual((Request.objects.count(), Task.objects.count(), mail.outbox), (0, 0, []))

    def test_a_deletion_is_remembered_by_the_next_press(self):
        response = self.post(dropped=["Photographer@s0"], drop="Photo Editor@s1")
        self.assertEqual(self.keys(response), ["Photographer@s1", "Event Coordinator", "Task Supervisor"])

    def test_restore_brings_the_task_and_its_editing_back(self):
        response = self.post(dropped=["Photographer@s0"], restore="Photographer@s0")
        self.assertEqual(self.keys(response), ["Photographer@s0", "Photo Editor@s0", "Photographer@s1", "Photo Editor@s1",
                                               "Event Coordinator", "Task Supervisor"])
        self.assertNotContains(response, 'name="dropped"')

    def test_deleting_an_editor_alone_keeps_its_shooter(self):
        response = self.post(drop="Photo Editor@s0")
        self.assertIn("Photographer@s0", self.keys(response))
        self.assertNotIn("Photo Editor@s0", self.keys(response))

    def test_the_coordinator_supervisor_and_unknown_tasks_cannot_be_deleted(self):
        response = self.post(dropped=["Event Coordinator", "Nonsense"], drop="Task Supervisor")
        self.assertIn("Event Coordinator", self.keys(response))
        self.assertIn("Task Supervisor", self.keys(response))
        self.assertEqual(response.context["dropped"], [])

    def test_choices_made_on_other_rows_survive_a_deletion(self):
        response = self.post(drop="Photographer@s0", picks={"Photographer@s1": self.vic.email})
        row = next(r for r in response.context["rows"] if r.key == "Photographer@s1")
        self.assertEqual(row.selected, self.vic.email)

    def test_saving_leaves_out_what_was_deleted(self):
        self.post(step="confirm", dropped=["Photographer@s0"], picks={"Photographer@s1": self.vic.email})
        request_obj = self.saved()
        self.assertFalse(request_obj.tasks.filter(sub_event__name="Opening").exists())
        self.assertTrue(request_obj.tasks.filter(task="Photographer", sub_event__name="Closing").exists())
        self.assertTrue(request_obj.tasks.filter(task="Event Coordinator").exists())

    def test_a_deletion_changes_the_coordinators_deadline(self):
        def due(response):
            return next(r.due for r in response.context["rows"] if r.key == "Event Coordinator")

        self.assertLess(due(self.post(dropped=["Photographer@s1"])), due(self.post()))

    def test_every_sub_event_has_a_plus_even_when_all_its_tasks_are_deleted(self):
        response = self.post(dropped=["Photographer@s0"])
        group = response.context["groups"][0]
        self.assertEqual((group.title, group.sub), ("Opening", "0"))
        self.assertContains(response, 'data-sub="0">+ Add a task to Opening')

    def test_a_task_added_with_a_plus_is_shown_under_that_sub_events_heading(self):
        response = self.post(extras=[("Photographer", "1", self.vic.email), ("Content Writer", "", "")])
        groups = {g.title: g for g in response.context["groups"]}
        self.assertEqual([r.task for r in groups["Closing"].extras], ["Photographer"])
        self.assertEqual([r.task for r in groups["Whole event"].extras], ["Content Writer"])
        self.assertEqual(groups["Opening"].extras, [])

    def test_a_single_day_event_has_one_untitled_group_with_a_plus(self):
        self.client.force_login(self.poc)
        start = timezone.now() + timedelta(days=5)
        page = self.client.post(self.url, {
            "type": "Coverage", "event_name": "One day", "event_kind": "single",
            "event_start": fmt(start), "event_end": fmt(start + timedelta(hours=2)),
            "venue": "Hall", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
            "drop": "Photographer",
        })
        self.assertEqual([g.title for g in page.context["groups"]], [""])
        self.assertContains(page, 'data-sub="">+ Add a task</button>')
        self.assertNotIn("Photographer", self.keys(page))

    def test_a_clubs_own_request_cannot_use_drop(self):
        self.client.force_login(self.club)
        self.client.post(reverse("request-new"), {**self.body(), "dropped": "Photographer@s0"})
        self.assertEqual(self.saved().tasks.filter(task="Photographer").count(), 2)


class AssignmentsAfterwardsTests(Base):
    def setUp(self):
        super().setUp()
        # Both sub-events pinned, so Pia (the person these tests then add) is not on it yet.
        self.post(step="confirm", picks={"Photographer@s0": self.ravi.email, "Photographer@s1": self.vic.email})
        self.request = self.saved()
        self.coordinator = User.objects.create_user("coord", email=self.request.coordinator_email)
        mail.outbox.clear()

    def add(self, user, **fields):
        self.client.force_login(user)
        return self.client.post(reverse("assignment-add", args=[self.request.pk]), fields)

    def test_a_photographer_can_be_added_again_even_though_the_request_has_one(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        offered = [entry["task_type"].task for entry in page.context["groups"][0]["add_forms"]]
        self.assertIn("Photographer", offered)
        self.assertNotIn("Task Supervisor", offered)  # one per request: reassign instead

    def test_the_add_form_offers_the_sub_events(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        self.assertContains(page, 'name="sub_event"')
        self.assertContains(page, "Opening")

    def test_the_coordinator_can_add_a_photographer_to_one_sub_event(self):
        closing = self.request.sub_events.get(name="Closing")
        self.add(self.coordinator, task_type="Photographer", sub_event=str(closing.pk), member_email=self.pia.email)
        added = self.request.tasks.get(task="Photographer", email=self.pia.email)
        self.assertEqual(added.sub_event, closing)
        self.assertEqual((added.event_start, added.venue), (closing.start, "Hall B"))
        self.assertEqual(added.deadline, closing.end)
        self.assertEqual(added.status, TaskStatus.CONFIRMED)
        editor = self.request.tasks.get(task="Photo Editor", paired_task=added)
        self.assertEqual((editor.email, editor.sub_event), (self.pia.email, closing))

    def test_the_new_person_is_emailed_for_both_tasks_naming_the_sub_event(self):
        closing = self.request.sub_events.get(name="Closing")
        self.add(self.coordinator, task_type="Photographer", sub_event=str(closing.pk), member_email=self.pia.email)
        subjects = [m.subject for m in mail.outbox if self.pia.email in m.to and m.subject.startswith("[Assigned]")]
        self.assertEqual(len(subjects), 2)
        self.assertTrue(all("Closing" in s for s in subjects))

    def test_leaving_it_on_the_whole_event_adds_a_whole_event_task(self):
        self.add(self.coordinator, task_type="Photographer", sub_event="", member_email=self.pia.email)
        self.assertIsNone(self.request.tasks.get(task="Photographer", email=self.pia.email).sub_event_id)

    def test_a_sub_event_of_another_request_is_refused(self):
        other = Request.objects.create(type="Coverage", event_name="Other", contact_email=COMMITTEE_EMAIL, is_multiday=True)
        foreign = SubEvent.objects.create(request=other, name="Foreign", start=at(12, 10), end=at(12, 11))
        response = self.add(self.coordinator, task_type="Photographer", sub_event=str(foreign.pk), member_email=self.pia.email)
        self.assertRedirects(response, reverse("assignment-detail", args=[self.request.pk]))
        self.assertFalse(self.request.tasks.filter(email=self.pia.email).exists())

    def test_someone_busy_in_that_sub_events_window_is_refused_when_picked_by_hand(self):
        closing = self.request.sub_events.get(name="Closing")

        class Busy(FreeCalendar):
            def is_free(self, email, start, end):
                return False

        with mock.patch("engine.assignment.calendar_service", return_value=Busy()):
            self.add(self.poc, task_type="Photographer", sub_event=str(closing.pk), member_email=self.pia.email)
        self.assertFalse(self.request.tasks.filter(email=self.pia.email).exists())


class AssignmentGroupsTests(Base):
    """The Assignments table is grouped by sub-event, each group with its own "+" and Delete buttons."""

    def setUp(self):
        super().setUp()
        self.post(step="confirm", picks={"Photographer@s0": self.ravi.email, "Photographer@s1": self.vic.email})
        self.request = self.saved()

    def page(self):
        self.client.force_login(self.poc)
        return self.client.get(reverse("assignment-detail", args=[self.request.pk]))

    def test_the_tasks_are_grouped_by_sub_event_then_the_whole_event(self):
        groups = self.page().context["groups"]
        self.assertEqual([g["title"] for g in groups], ["Opening", "Closing", "Whole event"])
        self.assertEqual(
            [
                sorted([(r["task"].task, r["task"].sub_event.name if r["task"].sub_event else None) for r in g["rows"]], key=str)
                for g in groups
            ],
            [
                [("Photo Editor", "Opening"), ("Photographer", "Opening")],
                [("Photo Editor", "Closing"), ("Photographer", "Closing")],
                [("Event Coordinator", None), ("Task Supervisor", None)],
            ],
        )

    def test_each_group_has_its_own_add_form_fixed_to_that_sub_event(self):
        page = self.page()
        groups = page.context["groups"]
        opening, closing = self.request.sub_events.get(name="Opening"), self.request.sub_events.get(name="Closing")
        self.assertEqual([g["sub_pk"] for g in groups], [str(opening.pk), str(closing.pk), ""])
        for group in groups:
            self.assertTrue(group["add_forms"])
        for value in (opening.pk, closing.pk, ""):
            self.assertContains(page, f'<input type="hidden" name="sub_event" value="{value}" />')
        self.assertContains(page, "+ Add a task to Opening")
        self.assertContains(page, "+ Add a task to Whole event")

    def test_a_task_added_from_a_groups_plus_lands_in_that_sub_event(self):
        closing = self.request.sub_events.get(name="Closing")
        self.client.force_login(self.poc)
        self.client.post(reverse("assignment-add", args=[self.request.pk]),
                         {"task_type": "Photographer", "sub_event": str(closing.pk), "member_email": self.pia.email})
        self.assertEqual(self.request.tasks.get(task="Photographer", email=self.pia.email).sub_event, closing)
        groups = self.page().context["groups"]
        self.assertEqual(len([r for r in groups[1]["rows"] if r["task"].task == "Photographer"]), 2)

    def test_every_deletable_row_has_a_delete_button_and_the_coordinator_does_not(self):
        page = self.page()
        for row in page.context["task_rows"]:
            url = reverse("assignment-remove", args=[row["task"].pk])
            if row["task"].task in ("Event Coordinator", "Task Supervisor"):
                self.assertNotContains(page, url)
            else:
                self.assertContains(page, url)

    def test_no_add_forms_once_the_request_is_closed(self):
        Request.objects.filter(pk=self.request.pk).update(status="Posted")
        self.assertTrue(all(not g["add_forms"] for g in self.page().context["groups"]))

    def test_a_single_day_event_is_one_untitled_group(self):
        start = timezone.now() + timedelta(days=6)
        self.client.force_login(self.poc)
        self.client.post(self.url, {
            "type": "Coverage", "event_name": "One day", "event_kind": "single", "step": "confirm",
            "event_start": fmt(start), "event_end": fmt(start + timedelta(hours=2)),
            "venue": "Hall", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
        })
        single = Request.objects.get(event_name="One day")
        groups = self.client.get(reverse("assignment-detail", args=[single.pk])).context["groups"]
        self.assertEqual([(g["title"], g["sub_pk"]) for g in groups], [("", "")])
        self.assertEqual(len(groups[0]["rows"]), 4)  # photographer, editor, coordinator, supervisor


class RemoveViewTests(Base):
    def setUp(self):
        super().setUp()
        self.post(step="confirm", picks={"Photographer@s0": self.ravi.email, "Photographer@s1": self.vic.email})
        self.request = self.saved()
        self.shooter = self.request.tasks.get(task="Photographer", sub_event__name="Closing")
        self.coordinator = User.objects.create_user("coord", email=self.request.coordinator_email)
        self.stranger = User.objects.create_user("stranger", email="stranger@iimsirmaur.ac.in")
        mail.outbox.clear()

    def remove(self, user, task=None):
        self.client.force_login(user)
        return self.client.post(reverse("assignment-remove", args=[(task or self.shooter).pk]))

    def test_the_coordinator_can_remove_a_photographer_and_their_editing(self):
        response = self.remove(self.coordinator)
        self.assertRedirects(response, reverse("assignment-detail", args=[self.request.pk]))
        self.assertFalse(Task.objects.filter(pk=self.shooter.pk).exists())
        self.assertFalse(self.request.tasks.filter(task="Photo Editor", sub_event__name="Closing").exists())
        self.assertTrue(self.request.tasks.filter(task="Photographer", sub_event__name="Opening").exists())

    def test_the_poc_can_too(self):
        self.remove(self.poc)
        self.assertFalse(Task.objects.filter(pk=self.shooter.pk).exists())

    def test_everyone_else_is_refused_and_nothing_changes(self):
        for user in (self.stranger, self.club):
            self.assertEqual(self.remove(user).status_code, 403, user.email)
        self.assertTrue(Task.objects.filter(pk=self.shooter.pk).exists())

    def test_it_only_takes_post(self):
        self.client.force_login(self.poc)
        self.assertEqual(self.client.get(reverse("assignment-remove", args=[self.shooter.pk])).status_code, 405)

    def test_every_task_but_the_coordinator_and_supervisor_gets_a_delete_button(self):
        self.client.force_login(self.coordinator)
        rows = {r["task"].task: r for r in self.client.get(
            reverse("assignment-detail", args=[self.request.pk])).context["task_rows"]}
        self.assertTrue(rows["Photographer"]["removable"])
        self.assertTrue(rows["Photo Editor"]["removable"])
        self.assertFalse(rows["Event Coordinator"]["removable"])
        self.assertFalse(rows["Task Supervisor"]["removable"])

    def test_an_editing_task_can_be_deleted_on_its_own(self):
        editor = self.request.tasks.get(task="Photo Editor", sub_event__name="Closing")
        self.remove(self.coordinator, editor)
        self.assertFalse(Task.objects.filter(pk=editor.pk).exists())
        self.assertTrue(Task.objects.filter(pk=self.shooter.pk).exists())

    def test_the_coordinator_and_supervisor_cannot_be_deleted_by_posting(self):
        for name in ("Event Coordinator", "Task Supervisor"):
            task = self.request.tasks.get(task=name)
            self.remove(self.poc, task)
            self.assertTrue(Task.objects.filter(pk=task.pk).exists(), name)

    def test_the_page_shows_a_remove_button_for_shooters(self):
        self.client.force_login(self.coordinator)
        page = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        self.assertContains(page, reverse("assignment-remove", args=[self.shooter.pk]))

    def test_a_task_that_is_done_shows_no_button_and_cannot_be_removed(self):
        Task.objects.filter(pk=self.shooter.pk).update(status=TaskStatus.DONE)
        self.client.force_login(self.coordinator)
        self.assertNotContains(
            self.client.get(reverse("assignment-detail", args=[self.request.pk])),
            reverse("assignment-remove", args=[self.shooter.pk]),
        )
        self.remove(self.coordinator)
        self.assertTrue(Task.objects.filter(pk=self.shooter.pk).exists())

    def test_the_message_says_what_was_removed(self):
        self.client.force_login(self.coordinator)
        response = self.client.post(reverse("assignment-remove", args=[self.shooter.pk]), follow=True)
        self.assertContains(response, "Removed: Photographer — Closing")

    def test_a_coordinator_cannot_remove_from_someone_elses_request(self):
        other = Request.objects.create(type="Coverage", event_name="Other", contact_email=COMMITTEE_EMAIL,
                                       status="Request Accepted", coordinator_email="someoneelse@iimsirmaur.ac.in")
        task = Task.objects.create(request=other, req_type="Coverage", ref_code="X_1", task="Photographer",
                                   email=self.ravi.email, member="Ravi", status="CONFIRMED")
        self.assertEqual(self.remove(self.coordinator, task).status_code, 403)
        self.assertTrue(Task.objects.filter(pk=task.pk).exists())


class SubEventViewsKeepTeamsInStepTests(Base):
    def setUp(self):
        super().setUp()
        self.post(step="confirm", picks={"Photographer@s0": self.ravi.email, "Photographer@s1": self.vic.email})
        self.request = self.saved()
        mail.outbox.clear()

    def test_adding_a_sub_event_staffs_it_and_says_who(self):
        start = at(10, 17)
        self.client.force_login(self.club)
        response = self.client.post(reverse("request-subevent-add", args=[self.request.pk]), {
            "new-name": "Gala", "new-start": fmt(start), "new-end": fmt(start + timedelta(hours=1)),
            "new-venue": "Lawn", "new-notes": "",
        }, follow=True)
        gala = self.request.sub_events.get(name="Gala")
        tasks = self.request.tasks.filter(sub_event=gala)
        self.assertEqual({t.task for t in tasks}, {"Photographer", "Photo Editor"})
        self.assertTrue(all(t.status == TaskStatus.CONFIRMED and t.email for t in tasks))
        self.assertContains(response, "Its team:")
        schedule = next(m for m in mail.outbox if m.subject.startswith("[Schedule updated]"))
        self.assertIn("Team assigned to it", schedule.body)

    def test_editing_a_sub_events_time_moves_its_team(self):
        closing = self.request.sub_events.get(name="Closing")
        new_start = closing.start + timedelta(hours=2)
        self.client.force_login(self.club)
        self.client.post(reverse("request-subevent-edit", args=[self.request.pk, closing.pk]), {
            f"se{closing.pk}-name": "Closing", f"se{closing.pk}-start": fmt(new_start),
            f"se{closing.pk}-end": fmt(new_start + timedelta(hours=2)), f"se{closing.pk}-venue": "Hall B",
            f"se{closing.pk}-notes": "",
        })
        shooter = self.request.tasks.get(task="Photographer", sub_event=closing)
        self.assertEqual(shooter.event_start, new_start.replace(second=0, microsecond=0))
        self.assertTrue(any("Was:" in m.body and self.vic.email in m.to for m in mail.outbox))

    def test_deleting_a_sub_event_releases_its_team(self):
        closing = self.request.sub_events.get(name="Closing")
        self.client.force_login(self.club)
        self.client.post(reverse("request-subevent-delete", args=[self.request.pk, closing.pk]))
        self.assertFalse(self.request.sub_events.filter(name="Closing").exists())
        self.assertFalse(self.request.tasks.filter(sub_event__name="Closing").exists())
        self.assertTrue(self.request.tasks.filter(sub_event__name="Opening").exists())
        self.assertTrue(any("cancelled" in m.subject and self.vic.email in m.to for m in mail.outbox))

    def test_the_48_hour_rule_still_protects_a_close_sub_event(self):
        soon = timezone.now() + timedelta(hours=20)
        near = SubEvent.objects.create(request=self.request, name="Near", start=soon, end=soon + timedelta(hours=1))
        Task.objects.create(request=self.request, req_type="Coverage", ref_code=self.request.ref_code, task="Photographer",
                            email=self.ravi.email, member="Ravi", status="CONFIRMED", sub_event=near)
        self.client.force_login(self.club)
        self.client.post(reverse("request-subevent-delete", args=[self.request.pk, near.pk]))
        self.assertTrue(SubEvent.objects.filter(pk=near.pk).exists())
        self.assertTrue(near.tasks.exists())

    def test_my_tasks_shows_the_sub_event_a_task_is_for(self):
        user = User.objects.create_user("ravi", email=self.ravi.email)
        self.client.force_login(user)
        page = self.client.get(reverse("task-list"))
        self.assertContains(page, "Photographer — Opening")
        self.assertContains(page, "Hall A")
