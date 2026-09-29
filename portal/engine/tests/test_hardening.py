"""
Regression tests for races and state-machine gaps found in the second review pass:
approve-vs-reject, filling an UNFILLED supervisor, posting without a maker, a
supervisor filled after the coordinator finished, and time-change edge cases.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from core.config import get_settings
from core.constants import RequestStatus, TaskStatus
from core.models import Request, TeamMember
from engine.assignment import override_proposed_assignee, perform_swap
from engine.confirm import confirm_request
from engine.event_changes import apply_event_time_change
from engine.workflow import complete_task, process_new_request, reject_request

from .factories import ASHA, NEHA, SUPERVISOR, FreeCalendar, build_world, coverage_request, post_request


class StatefulCalendar(FreeCalendar):
    """A calendar where a hold really does make its owner busy afterwards."""

    def __init__(self):
        self.holds = []

    def is_free(self, email, start, end):
        return not any(e == email and s < end and start < f for e, s, f in self.holds)

    def create_hold(self, **kwargs):
        self.holds.append((kwargs["email"], kwargs["start"], kwargs["end"]))


class ApproveRejectRaceTests(TestCase):
    def setUp(self):
        build_world()

    def test_rejecting_an_already_accepted_request_changes_nothing(self):
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.ACCEPTED)

        self.assertFalse(reject_request(request, "too late", "poc@iimsirmaur.ac.in"))
        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.ACCEPTED)

    def test_rejecting_twice_only_takes_effect_once(self):
        request = post_request()
        process_new_request(request)
        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.PENDING)
        self.assertTrue(reject_request(request, "no", "poc@iimsirmaur.ac.in"))
        self.assertFalse(reject_request(request, "no", "poc@iimsirmaur.ac.in"))

    def test_approving_a_request_that_was_rejected_first_does_not_resurrect_it(self):
        request = post_request()
        process_new_request(request)
        request.refresh_from_db()
        stale = Request.objects.get(pk=request.pk)  # the second approver's copy

        reject_request(request, "no", "poc@iimsirmaur.ac.in")
        confirm_request(stale)

        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.REJECTED)
        self.assertFalse(request.tasks.filter(status=TaskStatus.CONFIRMED).exists())


class SupervisorFillTests(TestCase):
    def setUp(self):
        build_world()

    def test_picking_a_supervisor_for_an_unfilled_slot_lets_approval_confirm_them(self):
        settings = get_settings()
        settings.require_approval_always = True
        settings.save()
        TeamMember.objects.filter(email=SUPERVISOR).update(active=False)
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        supervision = request.tasks.get(task="Task Supervisor")
        self.assertEqual(supervision.status, TaskStatus.UNFILLED)

        TeamMember.objects.filter(email=SUPERVISOR).update(active=True)
        override_proposed_assignee(supervision, TeamMember.objects.get(email=SUPERVISOR))
        confirm_request(request)

        supervision.refresh_from_db()
        self.assertEqual(supervision.status, TaskStatus.CONFIRMED)
        request.refresh_from_db()
        self.assertEqual(request.supervisor_email, SUPERVISOR)

    def test_a_supervisor_filled_after_the_coordinator_finished_is_closed_not_left_open(self):
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        request.tasks.filter(task="Event Coordinator").update(status=TaskStatus.DONE)
        supervision = request.tasks.get(task="Task Supervisor")
        newcomer = TeamMember.objects.create(
            email="sup2@iimsirmaur.ac.in", name="Second", campus="Permanent", year=2
        )

        perform_swap(supervision, newcomer, request)

        supervision.refresh_from_db()
        self.assertEqual(supervision.email, newcomer.email)
        self.assertEqual(supervision.status, TaskStatus.DONE)
        self.assertIsNotNone(supervision.completed_at)


class PostReadinessTests(TestCase):
    def test_a_post_does_not_go_out_while_a_maker_role_is_still_unfilled(self):
        build_world()
        request = post_request()
        process_new_request(request)
        confirm_request(request)
        request.tasks.filter(task="Graphic Designer").update(
            status=TaskStatus.UNFILLED, email="", member=""
        )

        writer = request.tasks.get(task="Content Writer")
        writer.status = TaskStatus.DONE
        writer.completed_at = timezone.now()
        writer.save()
        complete_task(writer)

        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.ACCEPTED)
        self.assertFalse(request.tasks.filter(task="Post").exists())


class ConcurrentAdvanceTests(TestCase):
    def test_a_request_is_advanced_to_scheduling_only_once(self):
        # Two makers' complete_task calls racing: each holds a copy of the request
        # still showing 'Request Accepted'. Only the first may run schedule_posts.
        build_world()
        request = post_request()
        process_new_request(request)
        confirm_request(request)
        first_copy = Request.objects.get(pk=request.pk)
        second_copy = Request.objects.get(pk=request.pk)
        for name in ("Graphic Designer", "Content Writer"):
            request.tasks.filter(task=name).update(status=TaskStatus.DONE, completed_at=timezone.now())

        writer = request.tasks.get(task="Content Writer")
        designer = request.tasks.get(task="Graphic Designer")
        writer.request = first_copy
        designer.request = second_copy
        complete_task(writer)
        complete_task(designer)

        self.assertEqual(request.tasks.filter(task="Post").count(), 1)


class TimeChangeEdgeCaseTests(TestCase):
    def setUp(self):
        build_world()

    def test_a_task_marked_late_is_reopened_when_the_new_deadline_is_in_the_future(self):
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        request.refresh_from_db()
        editor = request.tasks.get(task="Photo Editor")
        editor.status = TaskStatus.LATE
        editor.struck = True
        editor.save()

        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            apply_event_time_change(
                request, request.event_start, request.event_end + timedelta(hours=3), "club"
            )

        editor.refresh_from_db()
        self.assertEqual(editor.status, TaskStatus.CONFIRMED)
        self.assertFalse(editor.struck)

    def test_one_person_holding_two_at_event_tasks_does_not_clash_with_themselves(self):
        # With Neha gone, Asha is the only first-year: she doubles up as
        # Event Coordinator and Photographer.
        TeamMember.objects.filter(email=NEHA).update(active=False)
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        request.refresh_from_db()
        at_event = [t for t in request.tasks.all() if t.at_event]
        self.assertEqual({t.email for t in at_event}, {ASHA}, "fixture sanity check")
        self.assertGreaterEqual(len(at_event), 2, "fixture sanity check")

        calendar = StatefulCalendar()
        with mock.patch("engine.event_changes.calendar_service", return_value=calendar):
            result = apply_event_time_change(
                request, request.event_start, request.event_end + timedelta(hours=2), "club"
            )

        self.assertEqual(result["clashes"], [])
        self.assertEqual(len(calendar.holds), 1, "one hold per person, however many tasks")
