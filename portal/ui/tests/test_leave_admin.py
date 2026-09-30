"""
Views for Out-of-work requests (Profile -> Approvals), the tabbed Admin page, and
the "MBA 1st year" meeting shortcut.

Django's test client + locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import Availability, LeaveStatus
from core.models import LeaveRequest, Meeting, TeamMember
from engine.tests.factories import ASHA, COMMITTEE_EMAIL, NEHA, SUPERVISOR, build_world
from engine.tests.test_meetings_and_pairing import accepted_coverage

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
ADMIN = "admin@iimsirmaur.ac.in"


def today():
    return timezone.localdate()


def iso(day):
    return day.strftime("%Y-%m-%d")


class LeaveBase(TestCase):
    def setUp(self):
        build_world()
        self.neha = User.objects.create_user("neha", email=NEHA)
        self.asha = User.objects.create_user("asha", email=ASHA)
        self.poc = User.objects.create_user("poc", email=POC)
        self.admin = User.objects.create_user("admin", email=ADMIN)
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        mail.outbox.clear()

    def ask(self, user=None, **extra):
        body = {
            "reason": "Exams", "start_date": iso(today() + timedelta(days=3)), "end_date": iso(today() + timedelta(days=6)),
        }
        body.update(extra)
        self.client.force_login(user or self.neha)
        return self.client.post(reverse("leave-request"), body)

    def member(self):
        return TeamMember.objects.get(email=NEHA)


class ProfileTests(LeaveBase):
    def test_a_member_sees_the_request_form_and_a_club_does_not(self):
        self.client.force_login(self.neha)
        page = self.client.get(reverse("profile"))
        self.assertContains(page, "Request out of work")
        self.client.force_login(self.club)
        self.assertNotContains(self.client.get(reverse("profile")), "Request out of work")

    def test_asking_creates_a_pending_request_and_emails_the_poc(self):
        response = self.ask()
        self.assertRedirects(response, reverse("profile"))
        leave = LeaveRequest.objects.get()
        self.assertEqual((leave.status, leave.reason), (LeaveStatus.PENDING, "Exams"))
        self.assertEqual(self.member().availability, Availability.AVAILABLE)
        self.assertTrue(any(m.subject.startswith("[Out of work request]") and POC in m.to for m in mail.outbox))

    def test_the_pending_request_is_shown_with_a_withdraw_button(self):
        self.ask()
        page = self.client.get(reverse("profile"))
        self.assertContains(page, "waiting for the POC")
        self.assertContains(page, "Withdraw request")
        self.assertNotContains(page, "Request out of work")  # one at a time

    def test_bad_input_is_refused_with_a_message_and_nothing_saved(self):
        self.ask(reason="")
        self.ask(start_date=iso(today() - timedelta(days=2)))
        self.ask(end_date=iso(today() - timedelta(days=9)))
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_someone_not_on_the_team_cannot_ask(self):
        response = self.ask(self.club)
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_withdrawing(self):
        self.ask()
        leave = LeaveRequest.objects.get()
        self.client.post(reverse("leave-cancel", args=[leave.pk]))
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.CANCELLED)

    def test_nobody_can_withdraw_someone_elses_request(self):
        self.ask()
        leave = LeaveRequest.objects.get()
        self.client.force_login(self.asha)
        self.assertEqual(self.client.post(reverse("leave-cancel", args=[leave.pk])).status_code, 404)
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.PENDING)

    def test_im_back_appears_only_when_out_and_puts_them_on_work(self):
        self.client.force_login(self.neha)
        self.assertNotContains(self.client.get(reverse("profile")), "I'm back")
        TeamMember.objects.filter(email=NEHA).update(availability=Availability.OUT)
        self.assertContains(self.client.get(reverse("profile")), "I'm back")
        self.client.post(reverse("leave-return"))
        self.assertEqual(self.member().availability, Availability.AVAILABLE)

    def test_the_forms_only_take_post(self):
        self.client.force_login(self.neha)
        for name in ("leave-request", "leave-return"):
            self.assertEqual(self.client.get(reverse(name)).status_code, 405)


class ApprovalTests(LeaveBase):
    def setUp(self):
        super().setUp()
        self.ask()
        self.leave = LeaveRequest.objects.get()
        mail.outbox.clear()

    def decide(self, user, decision, note=""):
        self.client.force_login(user)
        return self.client.post(reverse("leave-decide", args=[self.leave.pk]), {"decision": decision, "note": note})

    def test_the_approvals_page_lists_it_with_the_open_tasks(self):
        TeamMember.objects.filter(email=ASHA).update(points=100)  # Neha coordinates
        accepted_coverage(roles_needed=[])
        self.client.force_login(self.poc)
        page = self.client.get(reverse("approval-list"))
        self.assertContains(page, "Out-of-work requests")
        self.assertContains(page, "Exams")
        self.assertContains(page, "Event Coordinator")  # her open task
        self.assertContains(page, "Approve")

    def test_poc_and_admin_can_approve(self):
        for user in (self.poc, self.admin):
            LeaveRequest.objects.filter(pk=self.leave.pk).update(status=LeaveStatus.PENDING)
            response = self.decide(user, "approve", note="ok")
            self.assertRedirects(response, reverse("approval-list"))
            self.leave.refresh_from_db()
            self.assertEqual((self.leave.status, self.leave.decided_by), (LeaveStatus.APPROVED, user.email))

    def test_approving_tells_the_member_and_they_stay_on_work_until_the_start_date(self):
        self.decide(self.poc, "approve", note="Take care")
        self.assertEqual(self.member().availability, Availability.AVAILABLE)  # starts in 3 days
        approved = next(m for m in mail.outbox if m.subject.startswith("[Out of work approved]"))
        self.assertEqual(approved.to, [NEHA])
        self.assertIn("Take care", approved.body)

    def test_approving_something_starting_today_switches_them_out(self):
        LeaveRequest.objects.filter(pk=self.leave.pk).update(start_date=today())
        self.decide(self.poc, "approve")
        self.assertEqual(self.member().availability, Availability.OUT)

    def test_declining(self):
        self.decide(self.poc, "decline", note="Sorry")
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, LeaveStatus.REJECTED)
        self.assertEqual(self.member().availability, Availability.AVAILABLE)
        self.assertTrue(any(m.subject.startswith("[Out of work declined]") for m in mail.outbox))

    def test_an_ordinary_member_and_a_head_cannot_decide(self):
        for user in (self.neha, self.asha, self.club):
            self.assertEqual(self.decide(user, "approve").status_code, 403, user.email)
        self.leave.refresh_from_db()
        self.assertEqual(self.leave.status, LeaveStatus.PENDING)

    def test_a_decided_request_leaves_the_list(self):
        self.decide(self.poc, "decline")
        page = self.client.get(reverse("approval-list"))
        self.assertNotContains(page, "Out-of-work requests")
        self.assertContains(page, "Nothing awaiting approval")


class AdminTabsTests(LeaveBase):
    def get(self, **params):
        self.client.force_login(self.poc)
        return self.client.get(reverse("portal-admin"), params)

    def test_team_is_the_default_and_shows_the_roster_strikes_and_a_status_toggle(self):
        page = self.get()
        self.assertContains(page, 'name="secondary_vertical"')
        self.assertContains(page, "Strikes and removal")
        self.assertContains(page, reverse("set-availability"))
        self.assertContains(page, "core/seed_data.py")
        self.assertNotContains(page, "Add / update committee")

    def test_each_tab_shows_only_its_own_section(self):
        self.assertContains(self.get(tab="committees"), "Add / update committee")
        self.assertNotContains(self.get(tab="committees"), 'name="secondary_vertical"')
        setup = self.get(tab="setup")
        for text in ("Vertical heads", "Import team from CSV", "Point scheme"):
            self.assertContains(setup, text)
        self.assertNotContains(setup, "Strikes and removal")

    def test_the_setup_extras_start_collapsed(self):
        page = self.get(tab="setup")
        self.assertContains(page, '<details class="fold">')
        self.assertNotContains(page, "<details open")

    def test_the_tab_bar_marks_the_current_tab(self):
        self.assertContains(self.get(tab="committees"), 'class="tab tab--active" href="/portal-admin/?tab=committees"')

    def test_an_unknown_tab_falls_back_to_team(self):
        self.assertContains(self.get(tab="nonsense"), "Team roster")

    def test_the_last_tab_is_remembered_so_actions_return_to_it(self):
        self.get(tab="committees")
        self.assertContains(self.client.get(reverse("portal-admin")), "Add / update committee")

    def test_marking_someone_out_from_the_roster_still_works_without_a_request(self):
        self.client.force_login(self.poc)
        self.client.post(reverse("set-availability"), {"member_email": NEHA, "availability": "out"})
        self.assertEqual(self.member().availability, Availability.OUT)

    def test_plain_members_are_still_kept_out(self):
        self.client.force_login(self.neha)
        self.assertEqual(self.client.get(reverse("portal-admin")).status_code, 403)


class MbaFirstYearMeetingTests(TestCase):
    def setUp(self):
        build_world()
        self.poc = User.objects.create_user("poc", email=POC)
        mk = lambda e, n, year, campus, **x: TeamMember.objects.create(email=e, name=n, year=year, campus=campus, **x)
        self.a = mk("a@iimsirmaur.ac.in", "MbaOne", 1, "MBA Campus")
        self.b = mk("b@iimsirmaur.ac.in", "MbaTwo", 1, "MBA Campus")
        mk("c@iimsirmaur.ac.in", "MbaSenior", 2, "MBA Campus")  # second-year
        mk("d@iimsirmaur.ac.in", "BmsOne", 1, "BMS Campus")  # other campus
        mk("e@iimsirmaur.ac.in", "MbaAway", 1, "MBA Campus", availability=Availability.OUT)
        mk("f@iimsirmaur.ac.in", "MbaGone", 1, "MBA Campus", active=False)
        mail.outbox.clear()

    def call(self, **extra):
        start = timezone.now() + timedelta(days=2)
        body = {
            "title": "MBA first-years", "start": timezone.localtime(start).strftime("%Y-%m-%dT%H:%M"),
            "end": timezone.localtime(start + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M"),
            "invite_mode": "mba_year1",
        }
        body.update(extra)
        self.client.force_login(self.poc)
        return self.client.post(reverse("meeting-new"), body)

    def test_it_is_offered_on_the_form(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("meeting-new"))
        self.assertContains(page, "MBA 1st year")
        self.assertContains(page, 'value="mba_year1"')

    def test_it_invites_only_active_on_work_first_years_on_the_mba_campus(self):
        self.call()
        invited = set(Meeting.objects.get().invites.values_list("member__email", flat=True))
        self.assertEqual(invited, {"a@iimsirmaur.ac.in", "b@iimsirmaur.ac.in"})

    def test_they_are_emailed_in_one_message(self):
        self.call()
        invite = next(m for m in mail.outbox if m.subject.startswith("[Meeting]"))
        self.assertEqual(set(invite.to), {"a@iimsirmaur.ac.in", "b@iimsirmaur.ac.in"})

    def test_it_can_be_combined_with_a_minutes_taker(self):
        self.call(wants_mom="on")
        self.assertIn(Meeting.objects.get().mom_email, {"a@iimsirmaur.ac.in", "b@iimsirmaur.ac.in"})

    def test_when_nobody_matches_it_says_so_instead_of_creating_an_empty_meeting(self):
        TeamMember.objects.filter(campus="MBA Campus", year=1).update(campus="BMS Campus")
        response = self.call()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(Meeting.objects.count(), 0)
