"""
The ported smoke test.

`server/smoke.ts` ran 12 checks against an in-memory MongoDB and was the gate
the Express port had to pass. These are those same 12 checks, in the same order,
against Django's throwaway test database — so a green run here means the
re-platform preserved the behaviour that mattered, end to end: reference-code
allocation, pipeline construction, assignment, confirmation, task completion,
post scheduling, and the short-notice approval gate.

Several checks have since been deliberately changed to follow the workflow as
it now is (a Post has no Vetter, a Coverage request has a Task Supervisor,
points are credited on completion, every Post needs approval) — they no longer
match the original byte for byte, by design.

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

from .factories import ASHA, NEHA, build_world, coverage_request, post_request


class PostFlowTests(TestCase):
    """Checks 1–5: a Post request from a committee, through approval to Posted."""

    def setUp(self):
        build_world()
        self.request = post_request()
        process_new_request(self.request)
        self.request.refresh_from_db()

    def test_reference_code_is_the_committees_first(self):
        self.assertEqual(self.request.ref_code, "MKTG_1")

    def test_post_request_requires_approval(self):
        # Post requests always get a POC/Secretary check before any team is
        # confirmed.
        self.assertEqual(self.request.status, RequestStatus.PENDING)

    def test_post_pipeline_is_content_writer_and_graphic_designer_only(self):
        # No vetting step and no Task Supervisor on a Post.
        names = sorted(self.request.tasks.values_list("task", flat=True))
        self.assertEqual(names, ["Content Writer", "Graphic Designer"])

    def test_makers_are_assigned_and_confirmed_on_approval(self):
        confirm_request(self.request)
        for name in ("Content Writer", "Graphic Designer"):
            task = self.request.tasks.get(task=name)
            self.assertEqual(task.status, TaskStatus.CONFIRMED)
            self.assertTrue(task.email)

    def _finish(self, name: str):
        task = self.request.tasks.get(task=name)
        task.status = TaskStatus.DONE
        task.completed_at = timezone.now()
        task.save()
        complete_task(task)

    def test_post_waits_for_both_makers_before_scheduling(self):
        confirm_request(self.request)
        self._finish("Graphic Designer")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, RequestStatus.ACCEPTED)  # writer still to go

        self._finish("Content Writer")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, RequestStatus.POSTED)

    def test_one_scheduled_post_task_is_created(self):
        confirm_request(self.request)
        self._finish("Graphic Designer")
        self._finish("Content Writer")

        posts = self.request.tasks.filter(task="Post")
        self.assertEqual(posts.count(), 1)
        self.assertEqual(posts.first().status, TaskStatus.SCHEDULED)


class LegacyVetterPostTests(TestCase):
    """A Post accepted before the Vetter was dropped still finishes the old way."""

    def test_completing_the_legacy_vetter_still_takes_it_to_posted(self):
        build_world()
        request = post_request()
        process_new_request(request)
        confirm_request(request)
        # Simulate a request from before the change: it has a Vetter task.
        Task.objects.create(
            request=request,
            req_type="Post",
            ref_code=request.ref_code,
            task="Vetter",
            member="Neha",
            email=NEHA,
            status=TaskStatus.CONFIRMED,
            created_at=timezone.now(),
        )
        vetter = request.tasks.get(task="Vetter")
        vetter.status = TaskStatus.DONE
        vetter.completed_at = timezone.now()
        vetter.save()
        complete_task(vetter)

        request.refresh_from_db()
        self.assertEqual(request.status, RequestStatus.POSTED)


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
        # request gets an Event Coordinator and a Task Supervisor.
        names = sorted(self.request.tasks.values_list("task", flat=True))
        self.assertEqual(
            names, ["Event Coordinator", "Photo Editor", "Photographer", "Task Supervisor"]
        )


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
    """Check 12: completing a task credits the assignee -- confirmation alone doesn't."""

    def test_assignee_accrues_points_only_after_completion(self):
        build_world()
        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        request.refresh_from_db()

        asha = TeamMember.objects.get(email=ASHA)
        self.assertEqual(asha.points, 0)

        tasks = list(request.tasks.filter(email=ASHA))
        self.assertTrue(tasks)  # sanity: she was actually assigned something
        for task in tasks:
            task.status = TaskStatus.DONE
            task.completed_at = timezone.now()
            task.save()
            complete_task(task)

        asha.refresh_from_db()
        self.assertGreater(asha.points, 0)
