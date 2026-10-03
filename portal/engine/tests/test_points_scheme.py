"""
`scheme_examples`: the rows the Admin page shows for "what the saved values pay".
They are computed with the engine's own functions, so these tests also pin the
default scheme's numbers.
"""

from __future__ import annotations

from django.test import TestCase

from core.models import PointsScheme
from engine.points import final_points, overdue_multiplier, scheme_examples


def scheme(**overrides):
    values = dict(
        coordinator_points=20, domain_task_points=10, vetter_points=10,
        early_window_hours=24, early_bonus_pct=30, late_threshold_hours=48,
        late_penalty_pct=30, subsequent_delay_hours=6, subsequent_penalty_pct=10,
    )
    values.update(overrides)
    return PointsScheme(**values)


class DefaultSchemeTests(TestCase):
    def test_a_normal_task(self):
        rows = scheme_examples(scheme())["task_rows"]
        self.assertEqual(
            rows,
            [
                ("Within 24 h", "+30%", 13),
                ("24–48 h", "no change", 10),
                ("Just past 48 h", "-30%", 7),
                ("54 h", "-40%", 6),
                ("66 h", "-60%", 4),
                ("90 h or more", "-100%", 0),
            ],
        )

    def test_the_event_coordinator(self):
        rows = scheme_examples(scheme())["coordinator_rows"]
        self.assertEqual(
            rows,
            [
                ("On time or early", "no change", 20),
                ("Just past its deadline", "-30%", 14),
                ("6 h late", "-40%", 12),
                ("18 h late", "-60%", 8),
                ("42 h late or more", "-100%", 0),
            ],
        )

    def test_the_base_shown_is_the_normal_task_base(self):
        self.assertEqual(scheme_examples(scheme(domain_task_points=14))["base"], 14)

    def test_it_agrees_with_the_engine_on_every_row(self):
        s = scheme()
        hours = {"Within 24 h": 24, "24–48 h": 48, "Just past 48 h": 48.5, "54 h": 54, "66 h": 66, "90 h or more": 90}
        for label, _, points in scheme_examples(s)["task_rows"]:
            self.assertEqual(points, final_points(10, hours[label], s), label)
        self.assertEqual(
            [p for _, _, p in scheme_examples(s)["coordinator_rows"]],
            [round(20 * overdue_multiplier(h, s)) for h in (0, 0.5, 6, 18, 42)],
        )


class UnusualSchemeTests(TestCase):
    def test_it_follows_the_saved_values(self):
        rows = scheme_examples(scheme(early_window_hours=12, early_bonus_pct=50, late_threshold_hours=72))["task_rows"]
        self.assertEqual(rows[0], ("Within 12 h", "+50%", 15))
        self.assertEqual(rows[1], ("12–72 h", "no change", 10))
        self.assertEqual(rows[2][0], "Just past 72 h")

    def test_a_first_penalty_of_100_percent_needs_no_separate_zero_row(self):
        rows = scheme_examples(scheme(late_penalty_pct=100))["task_rows"]
        self.assertEqual(rows[2], ("Just past 48 h", "-100%", 0))
        self.assertFalse([r for r in rows if r[0].endswith("or more")])

    def test_no_extra_penalty_step_means_it_never_reaches_zero(self):
        s = scheme(subsequent_penalty_pct=0)
        task, coordinator = scheme_examples(s)["task_rows"], scheme_examples(s)["coordinator_rows"]
        self.assertFalse([r for r in task if r[0].endswith("or more")])
        self.assertTrue(all(points > 0 for _, _, points in task if "Within" not in _))
        self.assertFalse([r for r in coordinator if r[0].endswith("or more")])

    def test_a_bonus_window_equal_to_the_late_line_leaves_no_on_time_band(self):
        rows = scheme_examples(scheme(early_window_hours=48))["task_rows"]
        self.assertEqual(rows[0][0], "Within 48 h")
        self.assertNotIn("48–48 h", [r[0] for r in rows])

    def test_a_zero_hour_block_is_treated_as_one_hour(self):
        # Misconfigured, but must not divide by zero or hang the page.
        rows = scheme_examples(scheme(subsequent_delay_hours=0))["task_rows"]
        self.assertTrue(rows)
