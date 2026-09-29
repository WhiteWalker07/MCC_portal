"""
Engine behaviour introduced with the Task Supervisor / two-verticals / time-change
work: who is eligible for what, in what order, and what a time change touches.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from core.config import get_settings, get_team
from core.constants import TaskStatus
from core.models import Request, TeamMember
from engine.assign import choose_supervisor, eligible_members, is_base_eligible
from engine.assignment import validate_member
from engine.event_changes import apply_event_time_change
from engine.workflow import open_supervision_counts, process_new_request

from .factories import (
    ASHA,
    BusyCalendar,
    FreeCalendar,
    SUPERVISOR,
    build_world,
    coverage_request,
)


def member(email, name, year=1, vertical="", secondary="", skills=(), points=0, **extra):
    return TeamMember.objects.create(
        email=email,
        name=name,
        year=year,
        campus="Permanent",
        vertical=vertical,
        secondary_vertical=secondary,
        skills=list(skills),
        points=points,
        **extra,
    )


class YearRuleTests(TestCase):
    def setUp(self):
        build_world()
        self.request = coverage_request(starts_in=timedelta(days=5))
        self.settings = get_settings()

    def test_second_year_is_eligible_only_for_the_supervisor_role(self):
        sup = TeamMember.objects.get(email=SUPERVISOR)
        self.assertTrue(is_base_eligible(sup, "", self.request, self.settings, task_name="Task Supervisor"))
        for task in ("Photographer", "Event Coordinator", "Graphic Designer", ""):
            self.assertFalse(
                is_base_eligible(sup, "", self.request, self.settings, require_skill=False, task_name=task),
                task,
            )

    def test_first_year_is_never_eligible_for_supervisor(self):
        asha = TeamMember.objects.get(email=ASHA)
        self.assertFalse(
            is_base_eligible(asha, "", self.request, self.settings, task_name="Task Supervisor")
        )

    def test_blank_required_skill_accepts_any_first_year(self):
        asha = TeamMember.objects.get(email=ASHA)
        self.assertTrue(is_base_eligible(asha, "", self.request, self.settings, task_name="Event Coordinator"))

    def test_hand_picking_a_second_year_for_ordinary_work_is_refused(self):
        verdict = validate_member(
            SUPERVISOR, "Photography", True, self.request, self.settings,
            require_skill=False, task_name="Photographer",
        )
        self.assertFalse(verdict.ok)
        self.assertIn("second-year", verdict.reason)

    def test_hand_picking_a_first_year_as_supervisor_is_refused(self):
        verdict = validate_member(
            ASHA, "", False, self.request, self.settings, require_skill=False, task_name="Task Supervisor"
        )
        self.assertFalse(verdict.ok)


class VerticalTierTests(TestCase):
    """Auto-assignment tries primary-vertical members, then secondary, then anyone else."""

    def test_primary_then_secondary_then_other_with_fairness_inside_each_tier(self):
        build_world()
        # Tier is decided before points: the primary-vertical member has the MOST points.
        primary = member("p@i.ac.in", "Primary", vertical="Content Writing", skills=["Content Writing"], points=50)
        secondary = member(
            "s@i.ac.in", "Secondary", vertical="Photography", secondary="Content Writing",
            skills=["Content Writing"], points=10,
        )
        other_busy = member("o1@i.ac.in", "OtherBusy", vertical="Photography", skills=["Content Writing"], points=5)
        other_free = member("o2@i.ac.in", "OtherFree", vertical="Photography", skills=["Content Writing"], points=0)

        request = coverage_request(starts_in=timedelta(days=5))
        pool = eligible_members(
            "Content Writing", False, request, get_settings(), get_team(), FreeCalendar(),
            task_name="Content Writer", vertical="Content Writing",
        )
        names = [m.name for m in pool]
        self.assertEqual(names[0], "Primary")
        self.assertEqual(names[1], "Secondary")
        # Everyone else (including the fixture's Neha, primary Graphic Designs) after them,
        # least points first.
        rest = names[2:]
        self.assertLess(rest.index("OtherFree"), rest.index("OtherBusy"))
        self.assertIn("Neha", rest)


class SupervisorChoiceTests(TestCase):
    def setUp(self):
        build_world()
        member("sup2@i.ac.in", "Zed Supervisor", year=2)

    def test_picks_the_second_year_with_the_fewest_open_supervisions(self):
        request = coverage_request(starts_in=timedelta(days=5))
        team = get_team()
        choice = choose_supervisor(request, get_settings(), team, {SUPERVISOR: 3, "sup2@i.ac.in": 1})
        self.assertEqual(choice.member.email, "sup2@i.ac.in")

    def test_successive_requests_spread_across_supervisors(self):
        first = coverage_request(starts_in=timedelta(days=5))
        process_new_request(first)
        second = coverage_request(starts_in=timedelta(days=6))
        process_new_request(second)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertTrue(first.supervisor_email)
        self.assertTrue(second.supervisor_email)
        self.assertNotEqual(first.supervisor_email, second.supervisor_email)

    def test_a_finished_or_rejected_request_does_not_count_as_open(self):
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        request.refresh_from_db()
        self.assertEqual(open_supervision_counts().get(request.supervisor_email), 1)

        request.status = "Rejected"
        request.save(update_fields=["status"])
        self.assertEqual(open_supervision_counts().get(request.supervisor_email, 0), 0)

    def test_no_second_year_leaves_the_supervisor_unfilled_with_a_reason(self):
        TeamMember.objects.filter(year=2).update(active=False)
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        supervision = request.tasks.get(task="Task Supervisor")
        self.assertEqual(supervision.status, TaskStatus.UNFILLED)
        self.assertIn("second-year", supervision.reason)


class TimeChangeTests(TestCase):
    def setUp(self):
        build_world()
        # Event: five days out, 10:00-12:00 local, so the arithmetic below is exact.
        day = (timezone.now() + timedelta(days=5)).astimezone(timezone.get_current_timezone())
        self.start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        self.end = self.start + timedelta(hours=2)
        self.request = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email="marketing@iimsirmaur.ac.in",
            venue="Hall", roles_needed=["Photographer"], platforms=[], status="New",
            event_start=self.start, event_end=self.end,
        )
        process_new_request(self.request)
        self.request.refresh_from_db()
        mail.outbox.clear()

    def test_updates_the_request_and_every_denormalised_task_copy(self):
        new_start, new_end = self.start + timedelta(hours=4), self.end + timedelta(hours=5)
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            apply_event_time_change(self.request, new_start, new_end, "club")
        self.request.refresh_from_db()
        self.assertEqual(self.request.event_start, new_start)
        self.assertEqual(self.request.event_end, new_end)
        for task in self.request.tasks.all():
            self.assertEqual(task.event_start, new_start)
            self.assertEqual(task.event_end, new_end)

    def test_deadlines_are_recomputed_from_the_new_end(self):
        new_end = self.end + timedelta(hours=3)
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            apply_event_time_change(self.request, self.start, new_end, "club")
        photographer = self.request.tasks.get(task="Photographer")  # at-event, due at the end
        editor = self.request.tasks.get(task="Photo Editor")  # end + 24h SLA
        supervision = self.request.tasks.get(task="Task Supervisor")
        self.assertEqual(photographer.deadline, new_end)
        self.assertEqual(editor.deadline, new_end + timedelta(hours=24))
        self.assertIsNone(supervision.deadline)

    def test_a_finished_task_keeps_its_deadline(self):
        editor = self.request.tasks.get(task="Photo Editor")
        editor.status = TaskStatus.DONE
        editor.save()
        old_deadline = editor.deadline
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            apply_event_time_change(self.request, self.start, self.end + timedelta(hours=3), "club")
        editor.refresh_from_db()
        self.assertEqual(editor.deadline, old_deadline)

    def test_team_supervisor_and_poc_are_emailed(self):
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            apply_event_time_change(self.request, self.start + timedelta(hours=1), self.end + timedelta(hours=1), "club")
        notice = next(m for m in mail.outbox if "[Time changed]" in m.subject)
        assigned = {t.email for t in self.request.tasks.all() if t.email}
        self.assertTrue(assigned <= set(notice.to), assigned - set(notice.to))
        self.assertIn(self.request.supervisor_email, notice.to)
        self.assertIn("poc@iimsirmaur.ac.in", notice.to)
        self.assertIn("The date is unchanged", notice.body)

    def test_moving_later_flags_a_clash_only_in_the_newly_covered_time(self):
        with mock.patch("engine.event_changes.calendar_service", return_value=BusyCalendar()):
            result = apply_event_time_change(
                self.request, self.start + timedelta(hours=1), self.end + timedelta(hours=1), "club"
            )
        self.assertTrue(result["clashes"])
        clash_mail = next(m for m in mail.outbox if "[Calendar clash]" in m.subject)
        self.assertIn(self.request.supervisor_email, clash_mail.to)
        self.assertIn("poc@iimsirmaur.ac.in", clash_mail.to)
        # Everyone stays assigned — a clash is flagged, never acted on.
        self.assertTrue(self.request.tasks.get(task="Photographer").email)

    def test_shrinking_inside_the_old_window_cannot_clash_with_yourself(self):
        # Even a calendar that says "busy" for everyone (their own old hold) must not
        # flag anyone when the new window lies wholly within the old one.
        with mock.patch("engine.event_changes.calendar_service", return_value=BusyCalendar()):
            result = apply_event_time_change(
                self.request, self.start + timedelta(minutes=30), self.end - timedelta(minutes=30), "club"
            )
        self.assertEqual(result["clashes"], [])
        self.assertFalse([m for m in mail.outbox if "[Calendar clash]" in m.subject])

    def test_before_acceptance_only_the_poc_is_told(self):
        pending = Request.objects.create(
            type="Coverage", event_name="Flash", contact_email="marketing@iimsirmaur.ac.in",
            roles_needed=["Photographer"], platforms=[], status="New",
            event_start=timezone.now() + timedelta(hours=5), event_end=timezone.now() + timedelta(hours=7),
        )
        process_new_request(pending)
        pending.refresh_from_db()
        self.assertEqual(pending.status, "Pending for POC approval")
        mail.outbox.clear()
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            apply_event_time_change(pending, pending.event_start + timedelta(hours=1), pending.event_end + timedelta(hours=1), "club")
        notice = next(m for m in mail.outbox if "[Time changed]" in m.subject)
        self.assertEqual(notice.to, ["poc@iimsirmaur.ac.in"])
