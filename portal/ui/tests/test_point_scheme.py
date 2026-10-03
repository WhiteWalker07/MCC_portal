"""
The Admin point-scheme card: grouping, help text, the example tables, validation
that stops a self-contradicting scheme, and the audit log of what changed.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from core.models import ActivityLog, PointsScheme
from engine.tests.factories import build_world

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
ADMIN = "admin@iimsirmaur.ac.in"

GOOD = {
    "coordinator_points": "20", "domain_task_points": "10", "vetter_points": "10",
    "early_window_hours": "24", "early_bonus_pct": "30", "late_threshold_hours": "48",
    "late_penalty_pct": "30", "subsequent_delay_hours": "6", "subsequent_penalty_pct": "10",
}


class Base(TestCase):
    def setUp(self):
        build_world()
        self.poc = User.objects.create_user("poc", email=POC)
        self.admin = User.objects.create_user("admin", email=ADMIN)

    def card(self, user=None):
        self.client.force_login(user or self.admin)
        return self.client.get(reverse("portal-admin"), {"tab": "setup"})

    def save(self, user=None, **changes):
        self.client.force_login(user or self.admin)
        return self.client.post(reverse("point-scheme"), {**GOOD, **changes}, follow=True)


class CardTests(Base):
    def test_it_is_grouped_and_every_field_has_help_text(self):
        page = self.card()
        for text in ("1. Base points", "2. Early bonus", "3. Late penalty", "Event Coordinator", "Every other task",
                     "Bonus window (hours)", "Late after (hours)", "Extra penalty each time (%)"):
            self.assertContains(page, text)
        self.assertContains(page, "never when it's assigned")  # the one-line rule at the top
        for field in page.context["point_form"]:
            self.assertTrue(field.help_text, field.name)

    def test_the_supervisor_is_shown_as_fixed_at_zero_and_has_no_input(self):
        page = self.card()
        self.assertContains(page, "Task Supervisor")
        self.assertContains(page, "always 0")
        self.assertNotIn("supervisor", [f.name for f in page.context["point_form"]])

    def test_the_vetter_value_is_labelled_as_old_requests_only(self):
        self.assertContains(self.card(), "Vetter (old requests only)")

    def test_the_example_tables_show_the_saved_values(self):
        page = self.card()
        self.assertContains(page, "What the saved values pay")
        self.assertContains(page, "Within 24 h")
        self.assertContains(page, "Just past its deadline")
        self.assertEqual(page.context["examples"]["base"], 10)

    def test_the_coordinator_deadline_hours_come_from_the_real_setting(self):
        self.assertContains(self.card(), "12 hours after the request")

    def test_the_examples_follow_a_change(self):
        self.save(domain_task_points="20", coordinator_points="40")
        examples = self.card().context["examples"]
        self.assertEqual(examples["task_rows"][0][2], 26)  # 20 x 1.30
        self.assertEqual(examples["coordinator_rows"][0][2], 40)

    def test_only_an_admin_sees_it(self):
        page = self.card(self.poc)
        self.assertNotContains(page, "1. Base points")
        self.assertContains(page, "Only admins can view or edit")


class ValidationTests(Base):
    def refused(self, **changes):
        before = PointsScheme.load().early_window_hours, PointsScheme.load().late_threshold_hours
        page = self.save(**changes)
        scheme = PointsScheme.load()
        self.assertEqual((scheme.early_window_hours, scheme.late_threshold_hours), before)
        self.assertContains(page, "Point scheme not saved")
        return page

    def test_a_bonus_window_longer_than_the_late_line_is_refused_with_the_reason(self):
        page = self.refused(early_window_hours="60", late_threshold_hours="48")
        self.assertContains(page, "Lateness can&#x27;t start before the bonus window ends")

    def test_percentages_over_100_are_refused(self):
        for field in ("early_bonus_pct", "late_penalty_pct", "subsequent_penalty_pct"):
            page = self.save(**{field: "150"})
            self.assertContains(page, "percentage from 0 to 100", msg_prefix=field)
        self.assertEqual(PointsScheme.load().late_penalty_pct, 30)

    def test_negative_values_are_refused(self):
        self.refused(coordinator_points="-5")
        self.refused(early_window_hours="-1")

    def test_absurd_point_values_are_refused(self):
        page = self.save(coordinator_points="999999")
        self.assertContains(page, "0 to 1000")
        self.assertEqual(PointsScheme.load().coordinator_points, 20)

    def test_a_zero_hour_penalty_step_is_refused(self):
        page = self.save(subsequent_delay_hours="0")
        self.assertContains(page, "at least 1 hour")
        self.assertEqual(PointsScheme.load().subsequent_delay_hours, 6)

    def test_the_message_names_the_field_not_just_check_the_values(self):
        page = self.save(early_bonus_pct="150")
        self.assertContains(page, "Early bonus (%)")

    def test_a_valid_change_is_saved(self):
        page = self.save(early_bonus_pct="40", late_penalty_pct="20")
        scheme = PointsScheme.load()
        self.assertEqual((scheme.early_bonus_pct, scheme.late_penalty_pct), (40, 20))
        self.assertContains(page, "Point scheme saved")

    def test_a_window_equal_to_the_late_line_is_allowed(self):
        self.save(early_window_hours="48", late_threshold_hours="48")
        self.assertEqual(PointsScheme.load().early_window_hours, 48)

    def test_only_an_admin_can_save(self):
        self.client.force_login(self.poc)
        self.assertEqual(self.client.post(reverse("point-scheme"), {**GOOD, "early_bonus_pct": "99"}).status_code, 403)
        self.assertEqual(PointsScheme.load().early_bonus_pct, 30)


class AuditLogTests(Base):
    def test_the_log_says_what_changed_and_who_changed_it(self):
        self.save(early_bonus_pct="40", coordinator_points="25")
        entry = ActivityLog.objects.filter(event="points-scheme").first()
        self.assertEqual(entry.actor, ADMIN)
        self.assertIn("Early bonus (%): 30 -> 40", entry.detail)
        self.assertIn("Event Coordinator: 20 -> 25", entry.detail)
        self.assertNotIn("Every other task", entry.detail)  # unchanged values aren't listed

    def test_saving_with_no_change_says_so(self):
        page = self.save()
        self.assertContains(page, "No changes to save")
        self.assertIn("no change", ActivityLog.objects.filter(event="points-scheme").first().detail)
