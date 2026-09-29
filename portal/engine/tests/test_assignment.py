"""
Regression tests for `perform_swap`'s bookkeeping (engine/assignment.py).

Reassigning a task must leave it in a state indistinguishable from a task
that had never been touched by anyone else: no leftover final/adjusted
points from a previous holder's completion, and no leftover deadline-strike
flag from a previous holder's miss.
"""

from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from core.config import get_points_scheme
from core.constants import TaskStatus
from engine.assignment import perform_swap
from engine.points import base_points_for
from engine.workflow import complete_task, process_new_request

from .factories import ASHA, NEHA, build_world, coverage_request


class ReassignBookkeepingTests(TestCase):
    def setUp(self):
        self.committee, self.asha, self.neha = build_world()
        self.request = coverage_request(starts_in=timedelta(hours=1))
        process_new_request(self.request)
        self.task = self.request.tasks.get(task="Photographer")
        self.assertEqual(self.task.email, ASHA, "fixture sanity check")

    def test_reassigning_a_completed_task_resets_points_to_the_role_base(self):
        # Complete it early enough to earn the timing bonus, so task.points
        # ends up holding an *adjusted* value, not the plain role base.
        self.task.status = TaskStatus.DONE
        self.task.completed_at = timezone.now()
        self.task.save()
        complete_task(self.task)
        self.task.refresh_from_db()
        self.assertTrue(self.task.points_awarded, "fixture sanity check")

        base = base_points_for("Photographer", get_points_scheme())
        self.assertNotEqual(self.task.points, base, "fixture sanity check: bonus must have applied")

        perform_swap(self.task, self.neha, self.request)
        self.task.refresh_from_db()

        self.assertFalse(self.task.points_awarded)
        self.assertFalse(self.task.timing_applied)
        self.assertEqual(
            self.task.points,
            base,
            "task.points must be reset to the role's base, or the next completion "
            "double-applies a timing multiplier on top of the old one",
        )

    def test_reassigning_a_struck_task_clears_the_strike_flag(self):
        self.task.status = TaskStatus.CONFIRMED
        self.task.struck = True
        self.task.save()

        perform_swap(self.task, self.neha, self.request)
        self.task.refresh_from_db()

        self.assertFalse(
            self.task.struck,
            "a new holder needs their own independent deadline watch, or "
            "run_deadline_check's struck=False filter never selects this task again",
        )

    def test_reassigning_a_never_completed_task_leaves_points_untouched(self):
        # No points were ever awarded, so there's nothing to reset — this
        # guards against the reset firing unconditionally.
        original_points = self.task.points
        perform_swap(self.task, self.neha, self.request)
        self.task.refresh_from_db()
        self.assertEqual(self.task.points, original_points)
