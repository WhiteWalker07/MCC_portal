"""
An additional Event Coordinator: added by the POC/Admin only, with all the main
coordinator's powers, scored on their own task, and closed (no points) when the other
coordinator hands the coverage to the club.

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
from core.models import ActivityLog, Request, Task, TeamMember
from engine.assignment import CoordinatorError, add_additional_coordinator, perform_swap, remove_additional_coordinator
from engine.tests.factories import ASHA, COMMITTEE_EMAIL, NEHA, SUPERVISOR, FreeCalendar, build_world, coverage_request
from engine.tests.test_meetings_and_pairing import member
from engine.workflow import process_new_request, run_deadline_check

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
ADMIN = "admin@iimsirmaur.ac.in"


class Base(TestCase):
    def setUp(self):
        build_world()
        TeamMember.objects.filter(email=ASHA).update(points=30)
        self.ravi = member("ravi@iimsirmaur.ac.in", "Ravi", vertical="Photography", skills=["Photography"])
        self.tom = member("tom@iimsirmaur.ac.in", "Tom", vertical="Videography")
        self.sue = member("sue@iimsirmaur.ac.in", "Sue", year=2)
        self.oli = member("oli@iimsirmaur.ac.in", "Oli", availability=Availability.OUT)
        self.poc = User.objects.create_user("poc", email=POC)
        self.admin = User.objects.create_user("admin", email=ADMIN)
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.request = coverage_request(starts_in=timedelta(days=6))
        with mock.patch("engine.workflow.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            process_new_request(self.request)
        self.request.refresh_from_db()
        self.main_email = self.request.coordinator_email
        self.main_user = User.objects.create_user("main", email=self.main_email)
        mail.outbox.clear()

    def add(self, email=None, actor=POC):
        email = email or self.ravi.email
        with mock.patch("engine.assignment.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            return add_additional_coordinator(self.request, email, actor)

    def co_task(self):
        return self.request.tasks.get(task="Event Coordinator", additional=True)

    def main_task(self):
        return self.request.tasks.get(task="Event Coordinator", additional=False)


class AddingTests(Base):
    def test_the_additional_coordinator_gets_their_own_task_and_is_recorded_on_the_request(self):
        task = self.add()
        self.request.refresh_from_db()
        self.assertEqual((task.email, task.additional, task.status), (self.ravi.email, True, TaskStatus.CONFIRMED))
        self.assertEqual(self.request.co_coordinator_email, self.ravi.email)
        self.assertEqual(self.request.coordinator_email, self.main_email)  # the main one is unchanged
        self.assertEqual(self.request.coordinator_emails, (self.main_email, self.ravi.email))

    def test_both_coordinators_are_due_at_the_same_time_12_hours_after_the_last_other_task(self):
        self.add()
        others = self.request.tasks.exclude(task__in=["Event Coordinator", "Task Supervisor"])
        expected = max(t.deadline for t in others) + timedelta(hours=12)
        self.assertEqual((self.main_task().deadline, self.co_task().deadline), (expected, expected))

    def test_they_are_told_and_so_is_the_club_and_the_clubs_team_list_gains_them(self):
        self.add()
        assigned = next(m for m in mail.outbox if m.subject.startswith("[Assigned]") and self.ravi.email in m.to)
        self.assertIn("Event Coordinator", assigned.subject)
        update = next(m for m in mail.outbox if m.subject.startswith("[Team update]"))
        self.assertEqual(update.to, [COMMITTEE_EMAIL])
        self.assertIn("also coordinating", update.body)
        self.request.refresh_from_db()
        self.assertIn((self.ravi.email, "Event Coordinator"), [(e["email"], e["role"]) for e in self.request.roster])

    def test_it_is_written_to_the_activity_log(self):
        self.add()
        self.assertTrue(ActivityLog.objects.filter(event="coordinator-added", actor=POC, member=self.ravi.email).exists())

    def test_before_the_request_is_accepted_they_are_only_proposed_and_nobody_is_emailed(self):
        Request.objects.filter(pk=self.request.pk).update(status="Pending for POC approval")
        self.request.refresh_from_db()
        task = self.add()
        self.assertEqual(task.status, TaskStatus.PROPOSED)
        self.assertEqual(mail.outbox, [])

    def test_a_second_additional_coordinator_is_refused(self):
        self.add()
        with self.assertRaises(CoordinatorError):
            self.add(self.tom.email)
        self.assertEqual(self.request.tasks.filter(task="Event Coordinator").count(), 2)

    def test_the_main_coordinator_cannot_also_be_the_additional_one(self):
        with self.assertRaises(CoordinatorError):
            self.add(self.main_email)

    def test_the_usual_rules_apply_to_who_can_be_added(self):
        for email in (self.oli.email, self.sue.email, "nobody@iimsirmaur.ac.in"):
            with self.assertRaises(CoordinatorError, msg=email):
                self.add(email)
        with self.assertRaises(CoordinatorError):
            add_additional_coordinator(self.request, "", POC)
        TeamMember.objects.filter(pk=self.ravi.pk).update(active=False)
        with self.assertRaises(CoordinatorError):
            self.add()
        self.assertFalse(self.request.tasks.filter(additional=True).exists())

    def test_only_a_coverage_request_that_is_still_open_can_have_one(self):
        Request.objects.filter(pk=self.request.pk).update(status="Posted")
        self.request.refresh_from_db()
        with self.assertRaises(CoordinatorError):
            self.add()
        post = Request.objects.create(type="Post", event_name="P", contact_email=COMMITTEE_EMAIL, status="Request Accepted")
        with self.assertRaises(CoordinatorError):
            add_additional_coordinator(post, self.ravi.email, POC)


class PowersTests(Base):
    def setUp(self):
        super().setUp()
        self.add()
        self.co_user = User.objects.create_user("co", email=self.ravi.email)
        mail.outbox.clear()

    def test_they_count_as_a_coordinator_and_can_open_the_request(self):
        self.client.force_login(self.co_user)
        self.assertEqual(self.client.get(reverse("request-detail", args=[self.request.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("assignment-detail", args=[self.request.pk])).status_code, 200)
        self.assertIn("Coordinator", self.client.get(reverse("profile")).context["role_badges"])

    def test_the_request_is_in_their_assignments_list_and_on_their_home(self):
        self.client.force_login(self.co_user)
        listing = self.client.get(reverse("assignment-list"))
        self.assertIn(self.request, list(listing.context["requests"]))
        home = self.client.get(reverse("home"))
        self.assertIn(self.request, home.context["coordinating"])
        self.assertContains(home, "you coordinate")

    def test_they_can_reassign_add_and_remove_tasks_like_the_main_coordinator(self):
        self.client.force_login(self.co_user)
        photographer = self.request.tasks.get(task="Photographer")
        reassign = self.client.post(reverse("assignment-reassign", args=[photographer.pk]), {"member_email": self.tom.email})
        self.assertEqual(reassign.status_code, 302)
        photographer.refresh_from_db()
        self.assertEqual(photographer.email, self.tom.email)
        self.client.post(reverse("assignment-add", args=[self.request.pk]),
                         {"task_type": "Photographer", "member_email": self.ravi.email})
        extra = self.request.tasks.filter(task="Photographer", email=self.ravi.email).first()
        self.assertIsNotNone(extra)
        self.assertEqual(self.client.post(reverse("assignment-remove", args=[extra.pk])).status_code, 302)
        self.assertFalse(Task.objects.filter(pk=extra.pk).exists())

    def test_they_can_mark_the_request_ready_and_edit_the_venue(self):
        from core.roles import can_assign, can_edit_venue, resolve_roles

        roles = resolve_roles(self.ravi.email)
        self.assertTrue(roles.is_coordinator)
        self.assertTrue(can_assign(roles, "", self.request.coordinator_emails))
        self.assertTrue(can_edit_venue(roles, self.request))

    def test_the_main_coordinator_has_the_same_powers_as_before(self):
        self.client.force_login(self.main_user)
        self.assertEqual(self.client.get(reverse("assignment-detail", args=[self.request.pk])).status_code, 200)

    def test_someone_who_is_neither_still_has_none(self):
        stranger = User.objects.create_user("stranger", email=self.tom.email)
        self.client.force_login(stranger)
        photographer = self.request.tasks.get(task="Photographer")
        self.assertEqual(self.client.post(reverse("assignment-reassign", args=[photographer.pk]), {"member_email": ""}).status_code, 403)
        self.assertEqual(self.client.get(reverse("assignment-detail", args=[self.request.pk])).status_code, 403)

    def test_they_get_late_notices_and_so_does_everyone_who_gets_them_today(self):
        Task.objects.filter(pk=self.main_task().pk).update(deadline=timezone.now() - timedelta(hours=1))
        run_deadline_check()
        late = next(m for m in mail.outbox if m.subject.startswith("[Late]"))
        self.assertIn(self.ravi.email, late.to)
        self.assertIn(self.main_email, late.to)

    def test_they_are_listed_with_an_additional_label_on_my_tasks(self):
        self.client.force_login(self.co_user)
        page = self.client.get(reverse("task-list"))
        self.assertContains(page, "Event Coordinator")
        self.assertContains(page, "(additional)")


class WhoCanAddOrRemoveTests(Base):
    def post(self, user, name, **data):
        self.client.force_login(user)
        return self.client.post(reverse(name, args=[self.request.pk]), data)

    def test_the_poc_and_admin_can_add_one(self):
        for user, who in ((self.poc, self.ravi.email), (self.admin, self.tom.email)):
            Request.objects.filter(pk=self.request.pk).update(co_coordinator_email="")
            self.request.tasks.filter(additional=True).delete()
            self.post(user, "assignment-add-coordinator", member_email=who)
            self.assertEqual(self.co_task().email, who, user.email)

    def test_the_main_coordinator_a_head_and_everyone_else_cannot(self):
        head = User.objects.create_user("head", email=ASHA)  # head of Photography in the fixture
        for user in (self.main_user, head, self.club):
            self.assertEqual(self.post(user, "assignment-add-coordinator", member_email=self.ravi.email).status_code, 403, user.email)
        self.assertFalse(self.request.tasks.filter(additional=True).exists())

    def test_nor_can_they_remove_one(self):
        self.add()
        for user in (self.main_user, self.club):
            self.assertEqual(self.post(user, "assignment-remove-coordinator").status_code, 403, user.email)
        self.assertTrue(self.request.tasks.filter(additional=True).exists())

    def test_the_additional_coordinator_cannot_remove_themselves_or_add_another(self):
        self.add()
        co = User.objects.create_user("co", email=self.ravi.email)
        self.assertEqual(self.post(co, "assignment-remove-coordinator").status_code, 403)
        self.assertEqual(self.post(co, "assignment-add-coordinator", member_email=self.tom.email).status_code, 403)

    def test_the_forms_only_take_post(self):
        self.client.force_login(self.poc)
        for name in ("assignment-add-coordinator", "assignment-remove-coordinator"):
            self.assertEqual(self.client.get(reverse(name, args=[self.request.pk])).status_code, 405)

    def test_a_blank_or_invalid_choice_is_refused_with_a_message(self):
        response = self.post(self.poc, "assignment-add-coordinator", member_email="")
        self.assertRedirects(response, reverse("assignment-detail", args=[self.request.pk]))
        self.assertFalse(self.request.tasks.filter(additional=True).exists())
        self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        response = self.post(self.poc, "assignment-add-coordinator", member_email=self.oli.email)
        self.assertFalse(self.request.tasks.filter(additional=True).exists())

    def test_the_page_offers_the_add_form_to_staff_only_and_not_once_there_is_one(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        self.assertContains(page, "Add additional coordinator")
        candidates = {e for e, _ in page.context["coordinator_form"].fields["member_email"].choices}
        self.assertIn(self.ravi.email, candidates)
        self.assertNotIn(self.main_email, candidates)
        self.assertNotIn(self.sue.email, candidates)
        self.assertNotIn(self.oli.email, candidates)
        self.client.force_login(self.main_user)
        self.assertNotContains(self.client.get(reverse("assignment-detail", args=[self.request.pk])), "Add additional coordinator")
        self.add()
        self.client.force_login(self.poc)
        after = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        self.assertNotContains(after, "Add additional coordinator")
        self.assertContains(after, "Remove additional coordinator")
        self.assertContains(after, "(additional)")

    def test_staff_are_told_why_when_a_request_cannot_have_one(self):
        self.client.force_login(self.admin)
        Request.objects.filter(pk=self.request.pk).update(status="Posted")
        closed = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        self.assertContains(closed, "Additional Event Coordinator")
        self.assertContains(closed, "can no longer be changed")
        self.assertNotContains(closed, "Add additional coordinator")
        post = Request.objects.create(type="Post", event_name="P", contact_email=COMMITTEE_EMAIL, status="Request Accepted")
        page = self.client.get(reverse("assignment-detail", args=[post.pk]))
        self.assertContains(page, "Only a Coverage request has an Event Coordinator")

    def test_the_main_coordinator_is_not_shown_the_section_at_all(self):
        self.client.force_login(self.main_user)
        self.assertNotContains(self.client.get(reverse("assignment-detail", args=[self.request.pk])), "Additional Event Coordinator")

    def add(self):
        with mock.patch("engine.assignment.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            return add_additional_coordinator(self.request, self.ravi.email, POC)

    def co_task(self):
        return self.request.tasks.get(task="Event Coordinator", additional=True)


class ReassigningTests(Base):
    def setUp(self):
        super().setUp()
        self.add()

    def test_replacing_the_additional_coordinator_changes_only_them(self):
        task = self.co_task()
        with mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            perform_swap(task, self.tom, self.request)
        self.request.refresh_from_db()
        self.assertEqual((self.request.co_coordinator_email, self.request.coordinator_email), (self.tom.email, self.main_email))
        self.assertEqual(self.main_task().email, self.main_email)

    def test_replacing_the_main_coordinator_changes_only_them(self):
        with mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            perform_swap(self.main_task(), self.tom, self.request)
        self.request.refresh_from_db()
        self.assertEqual((self.request.coordinator_email, self.request.co_coordinator_email), (self.tom.email, self.ravi.email))
        self.assertEqual(self.co_task().email, self.ravi.email)

    def test_the_dropdowns_never_offer_one_coordinator_the_other_ones_job(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        rows = [r for r in page.context["task_rows"] if r["task"].task == "Event Coordinator"]
        self.assertEqual(len(rows), 2)
        for row in rows:
            offered = {e for e, _ in row["form"].fields["member_email"].choices}
            self.assertNotIn(self.ravi.email, offered)
            self.assertNotIn(self.main_email, offered)

    def test_posting_the_other_coordinator_as_the_replacement_is_refused(self):
        self.client.force_login(self.poc)
        self.client.post(reverse("assignment-reassign", args=[self.main_task().pk]), {"member_email": self.ravi.email})
        self.assertEqual(self.main_task().email, self.main_email)

    def test_adding_a_coordinator_through_add_a_task_reassigns_the_main_one_not_the_additional(self):
        self.client.force_login(self.poc)
        self.client.post(reverse("assignment-add", args=[self.request.pk]),
                         {"task_type": "Event Coordinator", "member_email": self.tom.email})
        self.request.refresh_from_db()
        self.assertEqual((self.request.coordinator_email, self.request.co_coordinator_email), (self.tom.email, self.ravi.email))
        self.assertEqual(self.request.tasks.filter(task="Event Coordinator").count(), 2)

    def main_task(self):
        return self.request.tasks.get(task="Event Coordinator", additional=False)

    def co_task(self):
        return self.request.tasks.get(task="Event Coordinator", additional=True)

    def add(self):
        with mock.patch("engine.assignment.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            return add_additional_coordinator(self.request, self.ravi.email, POC)


class HandOffTests(Base):
    """Whichever coordinator shares the drive link, the other's task closes with no points."""

    def setUp(self):
        super().setUp()
        with mock.patch("engine.assignment.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            add_additional_coordinator(self.request, self.ravi.email, POC)
        # The event has started, so the tasks can be completed.
        past = timezone.now() - timedelta(hours=3)
        Task.objects.filter(request=self.request).update(event_start=past, event_end=past + timedelta(hours=1))
        self.co_user = User.objects.create_user("co", email=self.ravi.email)
        mail.outbox.clear()

    def hand_off(self, user, task):
        self.client.force_login(user)
        return self.client.post(reverse("task-complete", args=[task.pk]), {"content_links": "http://example.invalid/drive"})

    def points_of(self, email):
        return TeamMember.objects.get(email=email).points

    def test_the_main_coordinator_handing_over_closes_the_additional_ones_task_with_no_points(self):
        before_co = self.points_of(self.ravi.email)
        self.hand_off(self.main_user, self.main_task())
        co = self.co_task()
        self.assertEqual((co.status, co.points, co.points_awarded), (TaskStatus.DONE, 0, True))
        self.assertEqual(self.points_of(self.ravi.email), before_co)
        self.assertGreater(self.points_of(self.main_email), 0)

    def test_the_additional_coordinator_handing_over_closes_the_main_ones_and_earns_the_points(self):
        main_before = self.points_of(self.main_email)
        before = self.points_of(self.ravi.email)
        self.hand_off(self.co_user, self.co_task())
        main = self.main_task()
        self.assertEqual((main.status, main.points, main.points_awarded), (TaskStatus.DONE, 0, True))
        self.assertEqual(self.points_of(self.main_email), main_before)
        self.assertEqual(self.points_of(self.ravi.email), before + 20)

    def test_the_club_is_told_once_and_the_log_says_who_was_closed(self):
        self.hand_off(self.main_user, self.main_task())
        covered = [m for m in mail.outbox if m.subject.startswith("[Covered]")]
        self.assertEqual(len(covered), 1)
        self.assertTrue(ActivityLog.objects.filter(event="coordinator-closed", member=self.ravi.email).exists())

    def test_a_late_handover_costs_that_coordinator_points_on_the_usual_curve(self):
        # Both are due 12h after the last other task; finish 3 hours after that.
        task = self.co_task()
        Task.objects.filter(request=self.request, task="Event Coordinator").update(deadline=timezone.now() - timedelta(hours=3))
        before = self.points_of(self.ravi.email)
        self.hand_off(self.co_user, task)
        self.assertEqual(self.points_of(self.ravi.email) - before, 14)  # 20 less 30%

    def test_a_request_with_one_coordinator_behaves_exactly_as_before(self):
        remove_additional_coordinator(self.request, POC)
        before = self.points_of(self.main_email)
        self.hand_off(self.main_user, self.main_task())
        self.assertEqual(self.points_of(self.main_email) - before, 20)

    def test_a_second_hand_over_cannot_double_credit(self):
        self.hand_off(self.main_user, self.main_task())
        total = self.points_of(self.main_email)
        self.hand_off(self.main_user, self.main_task())
        self.assertEqual(self.points_of(self.main_email), total)


class RemovingTests(Base):
    def setUp(self):
        super().setUp()
        with mock.patch("engine.assignment.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            add_additional_coordinator(self.request, self.ravi.email, POC)
        mail.outbox.clear()

    def test_removing_clears_their_task_the_request_field_and_the_clubs_list(self):
        name = remove_additional_coordinator(self.request, POC)
        self.request.refresh_from_db()
        self.assertEqual(name, "Ravi")
        self.assertEqual(self.request.co_coordinator_email, "")
        self.assertEqual(self.request.coordinator_emails, (self.main_email,))
        self.assertFalse(self.request.tasks.filter(additional=True).exists())
        self.assertNotIn((self.ravi.email, "Event Coordinator"), [(e["email"], e["role"]) for e in self.request.roster])

    def test_they_and_the_club_are_told(self):
        remove_additional_coordinator(self.request, POC)
        self.assertTrue(any(m.subject.startswith("[Removed]") and self.ravi.email in m.to for m in mail.outbox))
        self.assertTrue(any(m.subject.startswith("[Team update]") and m.to == [COMMITTEE_EMAIL] for m in mail.outbox))

    def test_afterwards_another_can_be_added_and_they_lose_their_powers(self):
        remove_additional_coordinator(self.request, POC)
        from core.roles import resolve_roles

        self.assertFalse(resolve_roles(self.ravi.email).is_coordinator)
        with mock.patch("engine.assignment.calendar_service", return_value=FreeCalendar()), \
                mock.patch("engine.notify.calendar_service", return_value=FreeCalendar()):
            add_additional_coordinator(self.request, self.tom.email, POC)
        self.assertEqual(self.request.tasks.get(additional=True).email, self.tom.email)

    def test_nothing_to_remove_is_an_error(self):
        remove_additional_coordinator(self.request, POC)
        with self.assertRaises(CoordinatorError):
            remove_additional_coordinator(self.request, POC)

    def test_a_task_that_is_already_closed_cannot_be_removed(self):
        Task.objects.filter(request=self.request, additional=True).update(status=TaskStatus.DONE)
        with self.assertRaises(CoordinatorError):
            remove_additional_coordinator(self.request, POC)

    def test_the_view_removes_for_the_poc(self):
        self.client.force_login(self.poc)
        self.client.post(reverse("assignment-remove-coordinator", args=[self.request.pk]))
        self.assertFalse(self.request.tasks.filter(additional=True).exists())

    def test_the_activity_log_records_it(self):
        remove_additional_coordinator(self.request, POC)
        self.assertTrue(ActivityLog.objects.filter(event="coordinator-removed", actor=POC).exists())
