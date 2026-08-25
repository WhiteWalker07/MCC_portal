"""
The ported smoke test.

`server/smoke.ts` ran 12 checks against an in-memory MongoDB and was the gate
the Express port had to pass. These are those same 12 checks, in the same order,
against Django's throwaway test database — so a green run here means the
re-platform preserved the behaviour that mattered, end to end: reference-code
allocation, pipeline construction, assignment, confirmation, task completion,
post scheduling, and the short-notice approval gate.

Anything beyond the original 12 lives in the sibling test modules.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.constants import RequestStatus, TaskStatus
from core.models import Task, TeamMember
from engine.confirm import confirm_request
from engine.workflow import complete_task, process_new_request

from .factories import ASHA, build_world, coverage_request, post_request


class PostFlowTests(TestCase):
    """Checks 1–5: a Post request from a committee, all the way to Posted."""

    def setUp(self):
        build_world()
        self.request = post_request()
        process_new_request(self.request)
        self.request.refresh_from_db()

    def test_reference_code_is_the_committees_first(self):
        self.assertEqual(self.request.ref_code, "MKTG_1")

    def test_post_request_is_auto_accepted(self):
        # Post requests are never gated unless approval-always is on.
        self.assertEqual(self.request.status, RequestStatus.ACCEPTED)

    def test_vetter_is_assigned_and_confirmed(self):
        vetter = self.request.tasks.get(task="Vetter")
        self.assertEqual(vetter.status, TaskStatus.CONFIRMED)
        self.assertTrue(vetter.email)

    def test_completing_the_vetter_takes_the_request_to_posted(self):
        vetter = self.request.tasks.get(task="Vetter")
        vetter.status = TaskStatus.DONE
        vetter.completed_at = timezone.now()
        vetter.save()
        complete_task(vetter)

        self.request.refresh_from_db()
        self.assertEqual(self.request.status, RequestStatus.POSTED)

    def test_one_scheduled_post_task_is_created(self):
        vetter = self.request.tasks.get(task="Vetter")
        vetter.status = TaskStatus.DONE
        vetter.completed_at = timezone.now()
        vetter.save()
        complete_task(vetter)

        posts = self.request.tasks.filter(task="Post")
        self.assertEqual(posts.count(), 1)
        self.assertEqual(posts.first().status, TaskStatus.SCHEDULED)


class CoverageFarEventTests(TestCase):
    """Checks 6–9: a Coverage request for an event outside the notice window."""

    def setUp(self):
        build_world()
        # A prior Post request takes MKTG_1, so this one must be MKTG_2 —
        # the check that the committee's counter advances rather than resets.
        process_new_request(post_request())
        self.request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(self.request)
        self.request.refresh_from_db()

    def test_reference_code_increments_per_committee(self):
        self.assertEqual(self.request.ref_code, "MKTG_2")

    def test_far_off_event_is_accepted_without_approval(self):
        self.assertEqual(self.request.status, RequestStatus.ACCEPTED)

    def test_coordinator_is_stamped_on_the_request(self):
        self.assertTrue(self.request.coordinator_email)

    def test_pipeline_derives_the_photo_editor(self):
        # Asking for a Photographer implies a Photo Editor, and every Coverage
        # request gets an Event Coordinator.
        names = sorted(self.request.tasks.values_list("task", flat=True))
        self.assertEqual(names, ["Event Coordinator", "Photo Editor", "Photographer"])


class CoverageGatedEventTests(TestCase):
    """Checks 10–11: a short-notice event is held for the POC, then approved."""

    def setUp(self):
        build_world()
        self.request = coverage_request(
            starts_in=timedelta(hours=3),
            duration=timedelta(hours=1),
            event_name="Flash",
            venue="Lawn",
            platforms=[],
        )
        process_new_request(self.request)
        self.request.refresh_from_db()

    def test_short_notice_coverage_is_held_for_approval(self):
        self.assertEqual(self.request.status, RequestStatus.PENDING)

    def test_approval_moves_it_to_accepted(self):
        confirm_request(self.request)
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, RequestStatus.ACCEPTED)


class PointsAccrualTests(TestCase):
    """Check 12: confirmed assignments credit the assignee."""

    def test_assignee_accrues_points(self):
        build_world()
        process_new_request(coverage_request(starts_in=timedelta(days=5)))

        asha = TeamMember.objects.get(email=ASHA)
        self.assertGreater(asha.points, 0)
