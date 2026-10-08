"""
Entering a request for a club (POC/Admin), and the confirmation pop-up that follows
every successful submit.

Django's test client + locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import ActivityLog, Committee, Request
from engine.tests.factories import COMMITTEE_EMAIL, build_world

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
ADMIN = "admin@iimsirmaur.ac.in"


def fmt(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


class Base(TestCase):
    def setUp(self):
        build_world()
        self.committee = Committee.objects.get(email=COMMITTEE_EMAIL)
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.poc = User.objects.create_user("poc", email=POC)
        self.admin = User.objects.create_user("admin", email=ADMIN)
        self.plain = User.objects.create_user("plain", email="plain@iimsirmaur.ac.in")
        mail.outbox.clear()

    def url(self, for_committee=True):
        base = reverse("request-new")
        return f"{base}?for={self.committee.pk}" if for_committee else base

    def coverage(self, hours_away=24 * 5, **extra):
        start = timezone.now() + timedelta(hours=hours_away)
        body = {
            "type": "Coverage", "event_name": "Fest", "event_kind": "single",
            "event_start": fmt(start), "event_end": fmt(start + timedelta(hours=2)),
            "venue": "Auditorium", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
            "requester": "whatever",
        }
        body.update(extra)
        return body

    def post_for_club(self, user=None, **extra):
        """Fill the form and confirm the team, leaving everything on the system's picks."""
        self.client.force_login(user or self.poc)
        return self.client.post(self.url(), {**self.coverage(**extra), "step": "confirm"})


class ManualCreationAccessTests(Base):
    def test_the_picker_is_offered_to_poc_and_admin_only(self):
        for user, shown in ((self.poc, True), (self.admin, True), (self.club, False), (self.plain, False)):
            self.client.force_login(user)
            page = self.client.get(reverse("request-new"))
            self.assertEqual("enter a request for a club" in page.content.decode(), shown, user.email)

    def test_nobody_else_can_use_the_for_parameter(self):
        for user in (self.club, self.plain):
            self.client.force_login(user)
            self.assertEqual(self.client.get(self.url()).status_code, 403, user.email)
            self.assertEqual(self.client.post(self.url(), self.coverage()).status_code, 403, user.email)
        self.assertEqual(Request.objects.count(), 0)

    def test_a_bad_club_id_is_a_404(self):
        self.client.force_login(self.poc)
        self.assertEqual(self.client.get(reverse("request-new") + "?for=999999").status_code, 404)
        self.assertEqual(self.client.get(reverse("request-new") + "?for=abc").status_code, 404)

    def test_a_for_value_in_the_posted_body_does_nothing(self):
        self.client.force_login(self.plain)
        self.client.post(
            reverse("request-new"),
            {"type": "Post", "event_name": "Hi", "platforms": ["Instagram"],
             "content_links": "http://example.invalid/x", "requester": "Me", "for": str(self.committee.pk)},
        )
        request_obj = Request.objects.get()
        self.assertEqual(request_obj.contact_email, "plain@iimsirmaur.ac.in")
        self.assertEqual(request_obj.created_on_behalf_by, "")


class ManualCreationFormTests(Base):
    def test_the_form_behaves_as_that_club_with_a_banner_and_a_locked_name(self):
        self.client.force_login(self.poc)
        page = self.client.get(self.url())
        html = page.content.decode()
        self.assertContains(page, "Creating this request")
        self.assertContains(page, "Marketing")
        self.assertIn('value="Coverage"', html)  # Coverage offered although the POC isn't a committee
        start = html.index('name="requester"')
        tag = html[html.rindex("<input", 0, start): html.index("/>", start)]
        self.assertIn('value="Marketing"', tag)
        self.assertIn("readonly", tag)

    def test_without_it_a_poc_still_gets_the_ordinary_post_only_form(self):
        self.client.force_login(self.poc)
        html = self.client.get(reverse("request-new")).content.decode()
        self.assertNotIn('value="Coverage"', html)

    def test_the_picker_preselects_the_chosen_club(self):
        self.client.force_login(self.poc)
        self.assertContains(self.client.get(self.url()), f'value="{self.committee.pk}" selected')


class ManualCreationFlowTests(Base):
    def test_it_belongs_to_the_club_and_is_accepted_straight_away(self):
        response = self.post_for_club()
        self.assertRedirects(response, self.url())
        request_obj = Request.objects.get()
        self.assertEqual(request_obj.contact_email, COMMITTEE_EMAIL)
        self.assertEqual(request_obj.created_on_behalf_by, POC)
        self.assertTrue(request_obj.ref_code.startswith("MKTG_"))
        self.assertEqual(request_obj.status, "Request Accepted")

    def test_even_a_short_notice_event_skips_approval_unlike_the_clubs_own(self):
        self.post_for_club(hours_away=6, event_name="Rush")
        self.assertEqual(Request.objects.get(event_name="Rush").status, "Request Accepted")
        self.assertFalse([m for m in mail.outbox if "[Approval needed]" in m.subject])

        self.client.force_login(self.club)  # the same rush from the club itself is held for approval
        self.client.post(reverse("request-new"), self.coverage(hours_away=6, event_name="Rush Two"))
        self.assertEqual(Request.objects.get(event_name="Rush Two").status, "Pending for POC approval")

    def test_a_post_entered_for_a_club_is_accepted_without_the_usual_check(self):
        self.client.force_login(self.poc)
        self.client.post(
            self.url(),
            {"type": "Post", "event_name": "Launch", "platforms": ["Instagram"],
             "content_links": "http://example.invalid/x", "requester": "x", "step": "confirm"},
        )
        request_obj = Request.objects.get(event_name="Launch")
        self.assertEqual(request_obj.status, "Request Accepted")
        self.assertTrue(request_obj.ref_code.startswith("MKTG_"))

    def test_the_club_is_emailed_and_told_it_was_entered_on_its_behalf(self):
        self.post_for_club()
        accepted = next(m for m in mail.outbox if m.subject.startswith("[Accepted]"))
        self.assertEqual(accepted.to, [COMMITTEE_EMAIL])
        self.assertIn("entered this request on your behalf", accepted.body)

    def test_a_request_the_club_raises_itself_has_no_such_line(self):
        self.client.force_login(self.club)
        self.client.post(reverse("request-new"), self.coverage())
        accepted = next(m for m in mail.outbox if m.subject.startswith("[Accepted]"))
        self.assertNotIn("on your behalf", accepted.body)
        self.assertEqual(Request.objects.get().created_on_behalf_by, "")

    def test_the_requester_cannot_be_changed_it_is_the_clubs_name(self):
        self.post_for_club(requester="Somebody Else")
        self.assertEqual(Request.objects.get().requester, "Marketing")

    def test_it_shows_under_the_clubs_my_requests_and_notes_who_entered_it(self):
        self.post_for_club()
        request_obj = Request.objects.get()
        self.client.force_login(self.club)
        self.assertContains(self.client.get(reverse("request-list")), "Fest")
        detail = self.client.get(reverse("request-detail", args=[request_obj.pk]))
        self.assertContains(detail, "Entered by")
        self.assertContains(detail, POC)

    def test_the_audit_log_names_who_did_it(self):
        self.post_for_club(user=self.admin)
        entry = ActivityLog.objects.filter(event="created").first()
        self.assertEqual(entry.actor, ADMIN)
        self.assertIn(COMMITTEE_EMAIL, entry.detail)

    def test_a_poc_entering_their_own_post_is_unchanged(self):
        self.client.force_login(self.poc)
        self.client.post(
            reverse("request-new"),
            {"type": "Post", "event_name": "Mine", "platforms": ["Instagram"],
             "content_links": "http://example.invalid/x", "requester": "POC"},
        )
        request_obj = Request.objects.get()
        self.assertEqual((request_obj.contact_email, request_obj.created_on_behalf_by), (POC, ""))
        self.assertEqual(request_obj.status, "Pending for POC approval")

    def test_an_invalid_form_creates_nothing_and_stays_on_the_club(self):
        self.client.force_login(self.poc)
        response = self.client.post(self.url(), self.coverage(event_start="", event_name="Broken"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Creating this request")
        self.assertEqual(Request.objects.count(), 0)


class SubmittedPopupTests(Base):
    def submit_and_follow(self, user, body, url=None):
        self.client.force_login(user)
        return self.client.post(url or reverse("request-new"), body, follow=True)

    def test_after_a_submit_you_land_on_a_blank_form_with_a_popup_showing_the_id(self):
        page = self.submit_and_follow(self.club, self.coverage())
        request_obj = Request.objects.get()
        self.assertEqual(page.redirect_chain[-1][0], reverse("request-new"))
        self.assertContains(page, '<dialog id="submitted-dialog"')
        self.assertContains(page, request_obj.ref_code)
        self.assertContains(page, "OK")
        # The form is reset: nothing from the last submission is left in it.
        self.assertNotContains(page, 'value="Fest"')
        self.assertNotContains(page, 'value="Auditorium"')
        self.assertContains(page, 'name="event_name" value=""')

    def test_the_popup_shows_once_only(self):
        self.submit_and_follow(self.club, self.coverage())
        self.assertNotContains(self.client.get(reverse("request-new")), '<dialog id="submitted-dialog"')

    def test_a_refresh_after_submitting_cannot_resubmit(self):
        self.submit_and_follow(self.club, self.coverage())
        self.client.get(reverse("request-new"))
        self.assertEqual(Request.objects.count(), 1)

    def test_a_request_waiting_for_approval_says_so(self):
        page = self.submit_and_follow(
            self.plain,
            {"type": "Post", "event_name": "Launch", "platforms": ["Instagram"],
             "content_links": "http://example.invalid/x", "requester": "Me"},
        )
        self.assertContains(page, "with the POC for approval")

    def test_an_accepted_request_says_so(self):
        page = self.submit_and_follow(self.club, self.coverage())
        self.assertContains(page, "accepted and the team has been assigned")

    def test_a_request_entered_for_a_club_says_the_club_was_emailed_and_stays_on_that_club(self):
        page = self.submit_and_follow(self.poc, {**self.coverage(), "step": "confirm"}, url=self.url())
        self.assertContains(page, "for Marketing")
        self.assertContains(page, "the club has been emailed")
        self.assertEqual(page.redirect_chain[-1][0], self.url())
        self.assertContains(page, "Creating this request")  # ready for the next club request

    def test_a_form_with_errors_shows_no_popup_and_keeps_what_was_typed(self):
        self.client.force_login(self.club)
        response = self.client.post(reverse("request-new"), self.coverage(event_start="", event_name="Keep Me"))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, '<dialog id="submitted-dialog"')
        self.assertContains(response, "Keep Me")

    def test_the_popup_is_a_native_dialog_that_closes_without_javascript(self):
        page = self.submit_and_follow(self.club, self.coverage())
        html = page.content.decode()
        dialog = html[html.index('<dialog id="submitted-dialog"'): html.index("</dialog>")]
        self.assertIn(" open", dialog)  # visible even with scripting off
        self.assertIn('<form method="dialog"', dialog)  # its OK button closes it natively
