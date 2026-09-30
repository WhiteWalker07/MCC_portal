"""
End-to-end view smoke tests.

`manage.py check` validates Python wiring (URLs resolve, views import) but
never renders a template, so a bad `{% tag %}` or an undefined filter survives
it silently and only surfaces the first time a person loads the page. These
tests render every view template through the real Django test client — the
same gate `manage.py test` already runs — so template bugs are caught here
instead of on the lab PC.

Not a re-test of engine behaviour (engine/tests/test_smoke.py owns that): this
file exists to prove the views and templates that sit on top of the engine
actually render, for every role that can reach them.
"""

from __future__ import annotations

from datetime import time, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import RequestStatus, TaskStatus
from core.models import (
    Committee,
    PointsScheme,
    Platform,
    PortalSettings,
    PostSlot,
    Request,
    Task,
    TaskType,
    TeamMember,
)

User = get_user_model()

ADMIN_EMAIL = "admin@iimsirmaur.ac.in"
SECRETARY_EMAIL = "secretary@iimsirmaur.ac.in"
COMMITTEE_EMAIL = "sapient@iimsirmaur.ac.in"
MEMBER_EMAIL = "asha@iimsirmaur.ac.in"
OTHER_VERTICAL_EMAIL = "priyal@iimsirmaur.ac.in"
SAME_VERTICAL_EMAIL = "ishan@iimsirmaur.ac.in"
PLAIN_EMAIL = "student@iimsirmaur.ac.in"
GRAPHIC_DESIGNER_EMAIL = "sanjana@iimsirmaur.ac.in"
OTHER_GRAPHIC_DESIGNER_EMAIL = "aisha@iimsirmaur.ac.in"
SUPERVISOR_EMAIL = "supervisor@iimsirmaur.ac.in"
OTHER_SUPERVISOR_EMAIL = "supervisor2@iimsirmaur.ac.in"


class PortalViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for task, skill, points, sla, at_event, requestable, internal, vertical in [
            ("Photographer", "Photography", 5, 0, True, True, True, "Photography"),
            ("Photo Editor", "Photo Editing", 3, 24, False, False, True, "Photography"),
            ("Vetter", "Vetting", 2, 24, False, False, False, ""),
            ("Event Coordinator", "", 4, 0, True, False, True, ""),
            ("Graphic Designer", "Graphic design", 5, 24, False, False, True, "Graphic Designs"),
            ("Content Writer", "Content Writing", 3, 12, True, False, True, "Content Writing"),
            ("Task Supervisor", "", 0, 0, False, False, True, ""),
        ]:
            TaskType.objects.create(
                task=task,
                required_skill=skill,
                points=points,
                sla_hours=sla,
                at_event=at_event,
                requestable=requestable,
                internal_assignable=internal,
                vertical=vertical,
            )
        for hour in (11, 14, 17):
            PostSlot.objects.create(time=time(hour, 0))
        Platform.objects.create(platform="Instagram", handler_email=MEMBER_EMAIL, points=2, active=True)
        PointsScheme.load()

        settings_row = PortalSettings.load()
        settings_row.admin_emails = [ADMIN_EMAIL]
        settings_row.secretary_emails = [SECRETARY_EMAIL]
        settings_row.allowed_domains = ["iimsirmaur.ac.in"]
        settings_row.save()

        cls.committee = Committee.objects.create(
            email=COMMITTEE_EMAIL, name="Sapient", acronym="SPT", type="Club", campus="MBA Campus"
        )
        cls.member = TeamMember.objects.create(
            email=MEMBER_EMAIL,
            name="Asha",
            campus="MBA Campus",
            year=1,
            vertical="Photography",
            domain_head_of="Photography",
            skills=["Photography", "Photo Editing"],
        )
        # The only second-years: eligible as Task Supervisor and nothing else.
        cls.supervisor = TeamMember.objects.create(
            email=SUPERVISOR_EMAIL, name="Sup One", campus="MBA Campus", year=2, skills=[]
        )
        cls.other_supervisor = TeamMember.objects.create(
            email=OTHER_SUPERVISOR_EMAIL, name="Sup Two", campus="MBA Campus", year=2, skills=[]
        )
        # Deliberately no overlap with the Photography vertical's skills, so
        # this member is only reachable via the cross-vertical manual pick.
        cls.other_vertical_member = TeamMember.objects.create(
            email=OTHER_VERTICAL_EMAIL,
            name="Priyal Content",
            campus="MBA Campus",
            year=1,
            vertical="Content Writing",
            skills=["Content Writing"],
        )
        # In Asha's own vertical, so she (as its domain head) may strike them.
        cls.same_vertical_member = TeamMember.objects.create(
            email=SAME_VERTICAL_EMAIL,
            name="Ishan Junior",
            campus="MBA Campus",
            year=1,
            vertical="Photography",
            skills=["Photography"],
        )
        cls.graphic_designer = TeamMember.objects.create(
            email=GRAPHIC_DESIGNER_EMAIL,
            name="Sanjana Jaiswal",
            campus="MBA Campus",
            year=1,
            vertical="Graphic Designs",
            skills=["Graphic design"],
        )
        cls.other_graphic_designer = TeamMember.objects.create(
            email=OTHER_GRAPHIC_DESIGNER_EMAIL,
            name="Aisha Firdouse",
            campus="MBA Campus",
            year=1,
            vertical="Graphic Designs",
            skills=["Graphic design"],
        )

        cls.admin_user = User.objects.create_user("admin", email=ADMIN_EMAIL)
        cls.secretary_user = User.objects.create_user("secretary", email=SECRETARY_EMAIL)
        cls.committee_user = User.objects.create_user("committee", email=COMMITTEE_EMAIL)
        cls.member_user = User.objects.create_user("member", email=MEMBER_EMAIL)
        cls.plain_user = User.objects.create_user("plain", email=PLAIN_EMAIL)
        cls.supervisor_user = User.objects.create_user("supervisor", email=SUPERVISOR_EMAIL)
        cls.other_supervisor_user = User.objects.create_user("supervisor2", email=OTHER_SUPERVISOR_EMAIL)

    # ── profile ──────────────────────────────────────────────────────────────

    def test_profile_shows_team_member_info(self):
        self.client.force_login(self.member_user)
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Photography")
        self.assertContains(response, "pts")
        self.assertContains(response, "Team")

    def test_profile_shows_committee_info(self):
        self.client.force_login(self.committee_user)
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Sapient")
        self.assertContains(response, "SPT")

    def test_profile_renders_for_a_plain_user_with_no_roster_entry(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "isn't linked")

    def test_profile_requires_sign_in(self):
        response = self.client.get(reverse("profile"))
        self.assertEqual(response.status_code, 302)

    def test_team_member_can_update_their_own_phone_number(self):
        self.client.force_login(self.member_user)
        response = self.client.post(reverse("profile"), {"phone": "9876543210"})
        self.assertRedirects(response, reverse("profile"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.phone, "9876543210")

    # ── sign-in ──────────────────────────────────────────────────────────────

    def test_home_shows_signin_when_anonymous(self):
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Continue with Google")

    def test_home_redirects_when_authenticated(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("request-list"))

    # ── requests ─────────────────────────────────────────────────────────────

    def test_new_request_form_renders_for_plain_user(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("request-new"))
        self.assertEqual(response.status_code, 200)
        # Post-only for a non-committee: no radio option to pick Coverage, even
        # though the word "Coverage" legitimately appears elsewhere (JS, help text).
        self.assertNotContains(response, 'value="Coverage"')

    def test_new_request_form_offers_coverage_for_committee(self):
        self.client.force_login(self.committee_user)
        response = self.client.get(reverse("request-new"))
        self.assertContains(response, 'value="Coverage"')

    def test_invalid_coverage_submission_redisplays_the_form_without_crashing(self):
        # Regression test: a bound form redisplayed after validation failure
        # hands DateTimeLocalInput a raw POST string, not a datetime — it must
        # not crash trying to treat that string as one (ui/forms.py).
        self.client.force_login(self.committee_user)
        response = self.client.post(
            reverse("request-new"),
            {
                "type": "Coverage",
                "event_name": "Fest",
                "event_start": "2026-08-30T14:00",
                "event_end": "2026-08-30T10:00",  # before start — invalid
                "venue": "Auditorium",
                "roles_needed": ["Photographer"],
                "platforms": ["Instagram"],
                "requester": "Sapient",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must end after it starts")
        self.assertContains(response, "2026-08-30T14:00")

    def test_coverage_request_with_a_past_event_start_is_rejected(self):
        # A past event_start used to sail through validation, land as a
        # normal "New" -> gated Coverage request, and (because event_start
        # being before submission trivially satisfies "starts within 48h")
        # silently end up in the POC approval queue looking like a genuine
        # short-notice request instead of the obvious typo it is.
        self.client.force_login(self.committee_user)
        past = timezone.now() - timedelta(days=27)
        response = self.client.post(
            reverse("request-new"),
            {
                "type": "Coverage",
                "event_name": "Fest",
                "event_start": past.strftime("%Y-%m-%dT%H:%M"),
                "event_end": (past + timedelta(days=32)).strftime("%Y-%m-%dT%H:%M"),
                "venue": "Auditorium",
                "roles_needed": ["Photographer"],
                "platforms": ["Instagram"],
                "requester": "Sapient",
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "can&#x27;t be in the past")
        self.assertFalse(Request.objects.filter(event_name="Fest").exists())

    def test_plain_user_can_submit_a_post_request(self):
        self.client.force_login(self.plain_user)
        response = self.client.post(
            reverse("request-new"),
            {
                "type": "Post",
                "event_name": "Launch",
                "platforms": ["Instagram"],
                "content_links": "http://example.invalid/asset",
                "requester": "Student",
                "notes": "",
            },
        )
        request_obj = Request.objects.get(event_name="Launch")
        self.assertRedirects(response, reverse("request-detail", args=[request_obj.pk]))
        # Post requests always go through a POC/Secretary check now, so the
        # Graphic Designer can be confirmed or overridden at approval time.
        self.assertEqual(request_obj.status, RequestStatus.PENDING)
        self.assertTrue(request_obj.ref_code.startswith("MEDIA_"))

    def test_committee_can_submit_a_coverage_request(self):
        self.client.force_login(self.committee_user)
        start = timezone.now() + timedelta(days=5)
        response = self.client.post(
            reverse("request-new"),
            {
                "type": "Coverage",
                "event_name": "Fest",
                "event_start": start.strftime("%Y-%m-%dT%H:%M"),
                "event_end": (start + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M"),
                "venue": "Auditorium",
                "roles_needed": ["Photographer"],
                "platforms": ["Instagram"],
                "requester": "Sapient",
            },
        )
        request_obj = Request.objects.get(event_name="Fest")
        self.assertRedirects(response, reverse("request-detail", args=[request_obj.pk]))
        self.assertTrue(request_obj.ref_code.startswith("SPT_"))

    def test_committee_can_edit_the_venue(self):
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            status=RequestStatus.ACCEPTED,
            event_start=timezone.now() + timedelta(days=5),
            event_end=timezone.now() + timedelta(days=5, hours=2),
        )
        task = Task.objects.create(
            request=request_obj, req_type="Coverage", ref_code="SPT_1", task="Photographer",
            venue="Auditorium", email=MEMBER_EMAIL, member="Asha",
        )

        self.client.force_login(self.committee_user)
        response = self.client.post(
            reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Lawn (rain backup)"}
        )
        self.assertRedirects(response, reverse("request-detail", args=[request_obj.pk]))

        request_obj.refresh_from_db()
        task.refresh_from_db()
        self.assertEqual(request_obj.venue, "Lawn (rain backup)")
        self.assertEqual(task.venue, "Lawn (rain backup)", "task's denormalized venue must follow the request's")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(MEMBER_EMAIL, mail.outbox[0].to)
        self.assertIn("Venue changed", mail.outbox[0].subject)

    def test_coordinator_can_edit_the_venue(self):
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            coordinator_email=MEMBER_EMAIL,
            status=RequestStatus.ACCEPTED,
        )
        self.client.force_login(self.member_user)  # the coordinator
        response = self.client.post(
            reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Lawn"}
        )
        self.assertRedirects(response, reverse("request-detail", args=[request_obj.pk]))
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.venue, "Lawn")

    def test_domain_head_can_edit_the_venue_when_staffed_on_the_request(self):
        # Neither staff, second-year, nor the coordinator — access here must
        # come purely from being the domain head of a vertical actually
        # staffed on this request.
        head = TeamMember.objects.create(
            email="headonly@iimsirmaur.ac.in",
            name="Head Only",
            campus="MBA Campus",
            year=1,
            vertical="Content Writing",
            domain_head_of="Content Writing",
            skills=["Content Writing"],
        )
        head_user = User.objects.create_user("head-only", email=head.email)
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            status=RequestStatus.ACCEPTED,
        )
        Task.objects.create(
            request=request_obj, req_type="Coverage", ref_code="SPT_1", task="Content Writer",
            vertical="Content Writing", email=head.email, member=head.name,
        )

        self.client.force_login(head_user)
        response = self.client.post(
            reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Lawn"}
        )
        # Not asserting the redirect lands on a 200: can_edit_venue and
        # can_read_request are deliberately separate gates, and this member
        # isn't the request's requester/coordinator, so request-detail 403s
        # for them independently of whether the venue edit itself succeeded.
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("request-detail", args=[request_obj.pk]))
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.venue, "Lawn")

    def test_domain_head_of_an_unrelated_vertical_cannot_edit_the_venue(self):
        # year=1 deliberately, so is_second_year can't also grant access —
        # this isolates the domain-head branch itself.
        other_head = TeamMember.objects.create(
            email="otherhead@iimsirmaur.ac.in",
            name="Other Head",
            campus="MBA Campus",
            year=1,
            vertical="Photography",
            domain_head_of="Photography",
            skills=["Photography"],
        )
        other_head_user = User.objects.create_user("other-head", email=other_head.email)
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            venue="Auditorium", status=RequestStatus.ACCEPTED,
        )
        Task.objects.create(
            request=request_obj, req_type="Coverage", ref_code="SPT_1", task="Content Writer",
            vertical="Content Writing", email="someone@iimsirmaur.ac.in", member="Someone",
        )
        self.client.force_login(other_head_user)
        response = self.client.post(
            reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Lawn"}
        )
        self.assertEqual(response.status_code, 403)

    def test_stranger_cannot_edit_the_venue(self):
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            venue="Auditorium", status=RequestStatus.ACCEPTED,
        )
        self.client.force_login(self.plain_user)
        response = self.client.post(
            reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Lawn"}
        )
        self.assertEqual(response.status_code, 403)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.venue, "Auditorium")

    def test_venue_cannot_be_edited_on_a_post_request(self):
        request_obj = Request.objects.create(
            type="Post", event_name="Launch", contact_email=PLAIN_EMAIL, status=RequestStatus.ACCEPTED,
        )
        self.client.force_login(self.plain_user)
        response = self.client.post(
            reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Lawn"}
        )
        self.assertEqual(response.status_code, 403)

    def test_venue_cannot_be_edited_on_a_rejected_request(self):
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            venue="Auditorium", status=RequestStatus.REJECTED,
        )
        self.client.force_login(self.committee_user)
        response = self.client.post(
            reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Lawn"}
        )
        self.assertEqual(response.status_code, 403)

    def test_request_list_and_detail_render(self):
        self.client.force_login(self.plain_user)
        request_obj = Request.objects.create(
            type="Post",
            event_name="Launch",
            contact_email=PLAIN_EMAIL,
            platforms=["Instagram"],
            content_links="http://example.invalid",
            status=RequestStatus.NEW,
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)

        list_response = self.client.get(reverse("request-list"))
        self.assertContains(list_response, "Launch")

        detail_response = self.client.get(reverse("request-detail", args=[request_obj.pk]))
        self.assertEqual(detail_response.status_code, 200)

    def test_request_detail_denies_a_stranger(self):
        request_obj = Request.objects.create(
            type="Post", event_name="X", contact_email=PLAIN_EMAIL, status=RequestStatus.NEW
        )
        other = User.objects.create_user("other", email="other@iimsirmaur.ac.in")
        self.client.force_login(other)
        response = self.client.get(reverse("request-detail", args=[request_obj.pk]))
        self.assertEqual(response.status_code, 403)

    # ── tasks ────────────────────────────────────────────────────────────────

    def test_task_list_and_complete(self):
        request_obj = Request.objects.create(
            type="Post",
            event_name="Launch",
            contact_email=COMMITTEE_EMAIL,
            platforms=["Instagram"],
            content_links="http://example.invalid",
            status=RequestStatus.NEW,
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)
        task = request_obj.tasks.get(task="Content Writer")
        # Force Asha onto it directly so the completion path is under test,
        # independent of who the engine happened to auto-pick.
        task.email = MEMBER_EMAIL
        task.member = "Asha"
        task.status = TaskStatus.CONFIRMED
        task.save()

        self.client.force_login(self.member_user)
        list_response = self.client.get(reverse("task-list"))
        self.assertContains(list_response, "Content Writer")

        complete_response = self.client.post(reverse("task-complete", args=[task.pk]))
        self.assertRedirects(complete_response, reverse("task-list"))
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)

    def test_coordinator_completion_requires_drive_link_and_notifies_club(self):
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            status=RequestStatus.ACCEPTED,
            event_start=timezone.now() - timedelta(hours=3),
            event_end=timezone.now() - timedelta(hours=1),
        )
        task = Task.objects.create(
            request=request_obj,
            req_type="Coverage",
            ref_code="SPT_1",
            task="Event Coordinator",
            member="Asha",
            email=MEMBER_EMAIL,
            points=4,
            status=TaskStatus.CONFIRMED,
            event_start=request_obj.event_start,
            event_end=request_obj.event_end,
            event_name="Fest",
        )

        self.client.force_login(self.member_user)

        # No link -> refused, nothing changes, nobody's notified.
        refused = self.client.post(reverse("task-complete", args=[task.pk]))
        self.assertRedirects(refused, reverse("task-list"))
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.CONFIRMED)
        self.assertEqual(len(mail.outbox), 0)

        # With a link, it completes, the link lands on the request, and the
        # club is notified.
        completed = self.client.post(
            reverse("task-complete", args=[task.pk]),
            {"content_links": "https://drive.example/fest"},
        )
        self.assertRedirects(completed, reverse("task-list"))
        task.refresh_from_db()
        request_obj.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)
        self.assertEqual(request_obj.content_links, "https://drive.example/fest")
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn(COMMITTEE_EMAIL, mail.outbox[0].to)
        self.assertIn("Covered", mail.outbox[0].subject)
        self.assertIn("https://drive.example/fest", mail.outbox[0].body)

    def test_task_list_requires_team_membership(self):
        # Redirects to home(), which itself redirects an authenticated user on
        # to request-list — assert the immediate hop only, not the full chain.
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("task-list"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, reverse("home"))

    # ── assignments ──────────────────────────────────────────────────────────

    def test_assignment_flow(self):
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            roles_needed=["Photographer"],
            platforms=["Instagram"],
            status=RequestStatus.NEW,
            event_start=timezone.now() + timedelta(days=5),
            event_end=timezone.now() + timedelta(days=5, hours=2),
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)

        self.client.force_login(self.secretary_user)
        list_response = self.client.get(reverse("assignment-list"))
        self.assertContains(list_response, "Fest")

        detail_response = self.client.get(reverse("assignment-detail", args=[request_obj.pk]))
        self.assertEqual(detail_response.status_code, 200)

        task = request_obj.tasks.get(task="Photo Editor")
        reassign_response = self.client.post(
            reverse("assignment-reassign", args=[task.pk]), {"member_email": ""}
        )
        self.assertRedirects(reassign_response, reverse("assignment-detail", args=[request_obj.pk]))

    def test_manual_reassign_allows_a_cross_vertical_member(self):
        # A coordinator pulling in someone from an unrelated vertical as a
        # stopgap — previously blocked by the skill/vertical eligibility check.
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            roles_needed=["Photographer"],
            platforms=["Instagram"],
            status=RequestStatus.NEW,
            event_start=timezone.now() + timedelta(days=5),
            event_end=timezone.now() + timedelta(days=5, hours=2),
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)
        task = request_obj.tasks.get(task="Photographer")
        self.assertNotIn(
            "Photography", self.other_vertical_member.skills, "fixture sanity check"
        )

        self.client.force_login(self.secretary_user)

        # The dropdown itself must offer them, not just accept a hand-typed email.
        detail_response = self.client.get(reverse("assignment-detail", args=[request_obj.pk]))
        self.assertContains(detail_response, OTHER_VERTICAL_EMAIL)

        response = self.client.post(
            reverse("assignment-reassign", args=[task.pk]), {"member_email": OTHER_VERTICAL_EMAIL}
        )
        self.assertRedirects(response, reverse("assignment-detail", args=[request_obj.pk]))
        task.refresh_from_db()
        self.assertEqual(task.email, OTHER_VERTICAL_EMAIL)

    def test_auto_reassign_still_respects_the_skill_match(self):
        # Leaving the dropdown on its default ("auto-pick") must stay
        # skill-matched — only a deliberate manual pick may cross verticals.
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            roles_needed=["Photographer"],
            platforms=["Instagram"],
            status=RequestStatus.NEW,
            event_start=timezone.now() + timedelta(days=5),
            event_end=timezone.now() + timedelta(days=5, hours=2),
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)
        task = request_obj.tasks.get(task="Photographer")

        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("assignment-reassign", args=[task.pk]), {"member_email": ""}
        )
        self.assertRedirects(response, reverse("assignment-detail", args=[request_obj.pk]))
        task.refresh_from_db()
        # Asha held it, so auto-pick moves it to the other first-year with
        # "Photography" — never to Priyal (Content Writing only) or a second-year.
        self.assertEqual(task.email, SAME_VERTICAL_EMAIL)

    def test_assignment_list_forbidden_without_a_role(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("assignment-list"))
        self.assertEqual(response.status_code, 403)

    def test_assignment_detail_forbidden_for_a_stranger(self):
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            roles_needed=["Photographer"],
            platforms=["Instagram"],
            status=RequestStatus.NEW,
            event_start=timezone.now() + timedelta(days=5),
            event_end=timezone.now() + timedelta(days=5, hours=2),
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)

        # Not staff, not second-year, not a domain head, not this request's
        # coordinator, no task of theirs on it — no assignment role at all.
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("assignment-detail", args=[request_obj.pk]))
        self.assertEqual(response.status_code, 403)

    def test_assignment_detail_visible_to_the_coordinator(self):
        coordinator_member = TeamMember.objects.create(
            email="coordinator@iimsirmaur.ac.in",
            name="Coordinator Only",
            campus="MBA Campus",
            year=1,  # not a second-year, and not a domain head either
            vertical="Photography",
            skills=["Coordination"],
        )
        coordinator_user = User.objects.create_user(
            "coordinator-only", email=coordinator_member.email
        )
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            coordinator_email=coordinator_member.email,
            status=RequestStatus.ACCEPTED,
        )
        self.client.force_login(coordinator_user)
        response = self.client.get(reverse("assignment-detail", args=[request_obj.pk]))
        self.assertEqual(response.status_code, 200)

    def test_domain_head_cannot_add_a_task_to_a_request_outside_their_scope(self):
        head = TeamMember.objects.create(
            email="scopedhead@iimsirmaur.ac.in", name="Scoped Head", campus="MBA Campus",
            year=1, vertical="Photography", domain_head_of="Photography", skills=["Photography"],
        )
        head_user = User.objects.create_user("scoped-head", email=head.email)
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            venue="Auditorium", status=RequestStatus.ACCEPTED,
        )
        Task.objects.create(
            request=request_obj, req_type="Coverage", ref_code="SPT_1", task="Content Writer",
            vertical="Content Writing", email="someone@iimsirmaur.ac.in", member="Someone",
        )
        self.client.force_login(head_user)
        response = self.client.post(
            reverse("assignment-add", args=[request_obj.pk]), {"task_type": "Photographer"}
        )
        self.assertEqual(response.status_code, 403)
        self.assertFalse(request_obj.tasks.filter(task="Photographer").exists())

    def test_a_closed_request_cannot_be_reassigned(self):
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            venue="Auditorium", status=RequestStatus.POSTED,
        )
        task = Task.objects.create(
            request=request_obj, req_type="Coverage", ref_code="SPT_1", task="Photographer",
            vertical="Photography", email=MEMBER_EMAIL, member="Asha", status=TaskStatus.DONE,
        )
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("assignment-reassign", args=[task.pk]), {"member_email": SAME_VERTICAL_EMAIL},
            follow=True,
        )
        self.assertContains(response, "closed")
        task.refresh_from_db()
        self.assertEqual(task.email, MEMBER_EMAIL)

    def test_adding_an_event_coordinator_to_a_request_without_one_points_the_request_at_them(self):
        request_obj = Request.objects.create(
            type="Post", event_name="Launch", contact_email=COMMITTEE_EMAIL,
            status=RequestStatus.ACCEPTED,
        )
        other = Task.objects.create(
            request=request_obj, req_type="Post", ref_code="SPT_1", task="Content Writer",
            vertical="Content Writing", email="w@iimsirmaur.ac.in", member="W",
            status=TaskStatus.CONFIRMED,
        )
        self.client.force_login(self.secretary_user)
        self.client.post(
            reverse("assignment-add", args=[request_obj.pk]),
            {"task_type": "Event Coordinator", "member_email": SAME_VERTICAL_EMAIL},
        )
        request_obj.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(request_obj.coordinator_email, SAME_VERTICAL_EMAIL)
        self.assertEqual(other.coordinator_email, SAME_VERTICAL_EMAIL)

    def test_double_submitted_completion_credits_points_once(self):
        request_obj = Request.objects.create(
            type="Post", event_name="Launch", contact_email=COMMITTEE_EMAIL,
            platforms=["Instagram"], status=RequestStatus.ACCEPTED,
        )
        task = Task.objects.create(
            request=request_obj, req_type="Post", ref_code="SPT_1", task="Content Writer",
            vertical="Content Writing", email=MEMBER_EMAIL, member="Asha",
            status=TaskStatus.CONFIRMED, points=3, created_at=timezone.now(),
        )
        self.client.force_login(self.member_user)
        self.client.post(reverse("task-complete", args=[task.pk]))
        self.member.refresh_from_db()
        after_first = self.member.points

        self.client.post(reverse("task-complete", args=[task.pk]))
        self.member.refresh_from_db()
        self.assertEqual(self.member.points, after_first)

    def test_removing_a_strike_never_goes_below_zero(self):
        TeamMember.objects.filter(pk=self.same_vertical_member.pk).update(yellow_strikes=1)
        self.client.force_login(self.secretary_user)
        body = {"member_email": SAME_VERTICAL_EMAIL, "color": "yellow"}
        self.client.post(reverse("remove-strike"), body)
        # A stale second submission of the same form (the member no longer has
        # one): nothing to take away.
        TeamMember.objects.filter(pk=self.same_vertical_member.pk).update(red_strikes=1)
        self.client.post(reverse("remove-strike"), body)
        self.same_vertical_member.refresh_from_db()
        self.assertEqual(self.same_vertical_member.yellow_strikes, 0)

    def test_removing_a_member_warns_about_their_open_tasks(self):
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            venue="Auditorium", status=RequestStatus.ACCEPTED, ref_code="SPT_9",
        )
        Task.objects.create(
            request=request_obj, req_type="Coverage", ref_code="SPT_9", task="Photographer",
            vertical="Photography", email=SAME_VERTICAL_EMAIL, member="Ishan",
            status=TaskStatus.CONFIRMED,
        )
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("remove-from-team"), {"member_email": SAME_VERTICAL_EMAIL}, follow=True
        )
        self.assertContains(response, "SPT_9 Photographer")

    def test_a_removed_team_member_loses_task_access(self):
        TeamMember.objects.filter(pk=self.member.pk).update(active=False)
        self.client.force_login(self.member_user)
        response = self.client.get(reverse("task-list"))
        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)

    def test_only_staff_or_coordinators_see_the_mark_ready_button(self):
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            venue="Auditorium", coordinator_email="", supervisor_email=SUPERVISOR_EMAIL,
            status=RequestStatus.EVENT_COVERED,
        )
        self.client.force_login(self.supervisor_user)
        response = self.client.get(reverse("assignment-detail", args=[request_obj.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Mark ready to post")

        self.client.force_login(self.secretary_user)
        response = self.client.get(reverse("assignment-detail", args=[request_obj.pk]))
        self.assertContains(response, "Mark ready to post")

    # ── strikes ──────────────────────────────────────────────────────────────

    def test_secretary_can_give_a_red_strike_to_any_member(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("issue-strike"),
            {"member_email": OTHER_VERTICAL_EMAIL, "color": "red", "reason": "missed two deadlines"},
        )
        self.assertRedirects(response, reverse("assignment-list"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.red_strikes, 1)
        self.assertEqual(self.other_vertical_member.yellow_strikes, 0)

    def test_admin_can_give_a_yellow_strike_to_any_member(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "color": "yellow"}
        )
        self.assertRedirects(response, reverse("assignment-list"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.yellow_strikes, 1)
        self.assertEqual(self.other_vertical_member.red_strikes, 0)

    def test_domain_head_can_give_a_yellow_strike_within_their_own_vertical(self):
        self.client.force_login(self.member_user)  # Asha, head of Photography
        response = self.client.post(
            reverse("issue-strike"), {"member_email": SAME_VERTICAL_EMAIL, "color": "yellow"}
        )
        self.assertRedirects(response, reverse("assignment-list"))
        self.same_vertical_member.refresh_from_db()
        self.assertEqual(self.same_vertical_member.yellow_strikes, 1)

    def test_domain_head_cannot_give_a_red_strike(self):
        # Red is reserved to the POC/Admin — refused even inside their own vertical.
        self.client.force_login(self.member_user)  # Asha, head of Photography
        response = self.client.post(
            reverse("issue-strike"), {"member_email": SAME_VERTICAL_EMAIL, "color": "red"}
        )
        self.assertRedirects(response, reverse("assignment-list"))  # form rejects the colour
        self.same_vertical_member.refresh_from_db()
        self.assertEqual(self.same_vertical_member.red_strikes, 0)
        self.assertEqual(self.same_vertical_member.yellow_strikes, 0)

    def test_domain_head_cannot_strike_a_different_vertical(self):
        self.client.force_login(self.member_user)  # Asha, head of Photography
        response = self.client.post(
            reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "color": "yellow"}
        )
        # Not in Asha's dropdown at all, so the form itself rejects the choice.
        self.assertRedirects(response, reverse("assignment-list"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.yellow_strikes, 0)

    def test_domain_head_scope_includes_a_members_secondary_vertical(self):
        # Priyal's primary is Content Writing; give her Photography as her
        # secondary and Asha (head of Photography) may now yellow-strike her.
        self.other_vertical_member.secondary_vertical = "Photography"
        self.other_vertical_member.save()

        self.client.force_login(self.member_user)
        response = self.client.post(
            reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "color": "yellow"}
        )
        self.assertRedirects(response, reverse("assignment-list"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.yellow_strikes, 1)

    def test_strikes_do_not_stop_a_member_being_assigned(self):
        from core.config import get_settings
        from engine.assign import is_base_eligible

        self.member.yellow_strikes = 9
        self.member.red_strikes = 9
        self.member.save()
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL
        )
        self.assertTrue(is_base_eligible(self.member, "Photography", request_obj, get_settings()))

    def test_strike_form_is_not_offered_to_a_plain_coordinator(self):
        # Someone reaches /assignments/ as an event coordinator (via
        # can_reach_assignments) without being staff or a domain head — they
        # should see no strike form, and a direct POST must still be refused.
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL,
            coordinator_email=PLAIN_EMAIL, status=RequestStatus.ACCEPTED,
        )
        self.client.force_login(self.plain_user)
        list_response = self.client.get(reverse("assignment-list"))
        self.assertNotContains(list_response, 'action="/assignments/strike/"')

        response = self.client.post(
            reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "color": "yellow"}
        )
        self.assertEqual(response.status_code, 302)  # invalid form -> redirected, not applied
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.yellow_strikes, 0)

    # ── remove strike / remove from team (secretary + admin only) ──────────────

    def test_secretary_can_remove_a_yellow_strike(self):
        self.other_vertical_member.yellow_strikes = 2
        self.other_vertical_member.red_strikes = 1
        self.other_vertical_member.save()

        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("remove-strike"),
            {"member_email": OTHER_VERTICAL_EMAIL, "color": "yellow", "reason": "issued in error"},
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.yellow_strikes, 1)
        self.assertEqual(self.other_vertical_member.red_strikes, 1)  # the other colour untouched

    def test_admin_can_remove_a_red_strike(self):
        self.other_vertical_member.red_strikes = 1
        self.other_vertical_member.save()

        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("remove-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "color": "red"}
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.red_strikes, 0)

    def test_removing_a_colour_the_member_does_not_have_changes_nothing(self):
        self.other_vertical_member.yellow_strikes = 1  # a yellow, but no red
        self.other_vertical_member.save()

        self.client.force_login(self.admin_user)
        self.client.post(reverse("remove-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "color": "red"})
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.red_strikes, 0)
        self.assertEqual(self.other_vertical_member.yellow_strikes, 1)

    def test_domain_head_cannot_remove_a_strike(self):
        # Narrower than giving one: removal is secretary/admin only, no
        # domain-head carve-out, even within their own vertical.
        self.same_vertical_member.yellow_strikes = 1
        self.same_vertical_member.save()

        self.client.force_login(self.member_user)  # Asha, head of Photography
        response = self.client.post(
            reverse("remove-strike"), {"member_email": SAME_VERTICAL_EMAIL, "color": "yellow"}
        )
        self.assertEqual(response.status_code, 403)
        self.same_vertical_member.refresh_from_db()
        self.assertEqual(self.same_vertical_member.yellow_strikes, 1)

    def test_remove_strike_form_is_not_offered_when_nobody_has_one(self):
        self.client.force_login(self.secretary_user)
        response = self.client.get(reverse("portal-admin"))
        self.assertNotContains(response, 'action="/portal-admin/remove-strike/"')

    def test_secretary_can_remove_someone_from_the_team(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("remove-from-team"), {"member_email": SAME_VERTICAL_EMAIL, "reason": "left the institute"}
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.same_vertical_member.refresh_from_db()
        self.assertFalse(self.same_vertical_member.active)

    def test_removing_a_domain_head_clears_their_headship(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("remove-from-team"), {"member_email": MEMBER_EMAIL})
        self.assertRedirects(response, reverse("portal-admin"))
        self.member.refresh_from_db()
        self.assertFalse(self.member.active)
        self.assertEqual(self.member.domain_head_of, "")

    def test_removal_keeps_points_and_strikes_intact(self):
        self.member.points = 50
        self.member.yellow_strikes = 2
        self.member.red_strikes = 1
        self.member.save()

        self.client.force_login(self.admin_user)
        self.client.post(reverse("remove-from-team"), {"member_email": MEMBER_EMAIL})
        self.member.refresh_from_db()
        self.assertFalse(self.member.active)
        self.assertEqual(self.member.points, 50)
        self.assertEqual(self.member.yellow_strikes, 2)
        self.assertEqual(self.member.red_strikes, 1)

    def test_domain_head_cannot_remove_someone_from_the_team(self):
        self.client.force_login(self.member_user)  # Asha, head of Photography, not staff
        response = self.client.post(reverse("remove-from-team"), {"member_email": SAME_VERTICAL_EMAIL})
        self.assertEqual(response.status_code, 403)
        self.same_vertical_member.refresh_from_db()
        self.assertTrue(self.same_vertical_member.active)

    def test_plain_user_cannot_reach_either_action(self):
        self.client.force_login(self.plain_user)
        self.assertEqual(
            self.client.post(reverse("remove-strike"), {"member_email": OTHER_VERTICAL_EMAIL}).status_code, 403
        )
        self.assertEqual(
            self.client.post(reverse("remove-from-team"), {"member_email": OTHER_VERTICAL_EMAIL}).status_code, 403
        )

    # ── approvals ────────────────────────────────────────────────────────────

    def test_approval_flow(self):
        request_obj = Request.objects.create(
            type="Coverage",
            event_name="Flash",
            contact_email=COMMITTEE_EMAIL,
            venue="Lawn",
            roles_needed=["Photographer"],
            platforms=[],
            status=RequestStatus.NEW,
            event_start=timezone.now() + timedelta(hours=3),
            event_end=timezone.now() + timedelta(hours=4),
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatus.PENDING)

        self.client.force_login(self.secretary_user)
        list_response = self.client.get(reverse("approval-list"))
        self.assertContains(list_response, "Flash")

        detail_response = self.client.get(reverse("approval-detail", args=[request_obj.pk]))
        self.assertEqual(detail_response.status_code, 200)

        decide_response = self.client.post(
            reverse("approval-decide", args=[request_obj.pk]), {"decision": "approve"}
        )
        self.assertRedirects(decide_response, reverse("approval-list"))
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatus.ACCEPTED)

    def test_post_pipeline_is_content_writer_and_graphic_designer_only(self):
        request_obj = Request.objects.create(
            type="Post",
            event_name="Launch",
            contact_email=COMMITTEE_EMAIL,
            platforms=["Instagram"],
            content_links="http://example.invalid/asset",
            status=RequestStatus.NEW,
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)
        task_names = set(request_obj.tasks.values_list("task", flat=True))
        # No Vetter and no Task Supervisor on a Post.
        self.assertEqual(task_names, {"Content Writer", "Graphic Designer"})

    def _pending_post(self):
        request_obj = Request.objects.create(
            type="Post",
            event_name="Launch",
            contact_email=COMMITTEE_EMAIL,
            platforms=["Instagram"],
            content_links="http://example.invalid/asset",
            status=RequestStatus.NEW,
        )
        from engine.workflow import process_new_request

        process_new_request(request_obj)
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatus.PENDING)
        return request_obj

    def test_approval_page_for_a_post_has_no_designer_or_supervisor_picker(self):
        request_obj = self._pending_post()
        self.client.force_login(self.secretary_user)
        response = self.client.get(reverse("approval-detail", args=[request_obj.pk]))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Task Supervisor (a 2nd-year)")
        self.assertNotContains(response, 'name="member_email"')

    def test_approving_a_post_tells_the_graphic_designs_head_who_was_picked(self):
        self.graphic_designer.domain_head_of = "Graphic Designs"
        self.graphic_designer.save()
        request_obj = self._pending_post()

        self.client.force_login(self.secretary_user)
        mail.outbox.clear()
        response = self.client.post(reverse("approval-decide", args=[request_obj.pk]), {"decision": "approve"})
        self.assertRedirects(response, reverse("approval-list"))

        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatus.ACCEPTED)
        notices = [m for m in mail.outbox if "[Post approved]" in m.subject]
        self.assertEqual(len(notices), 1)
        self.assertIn(GRAPHIC_DESIGNER_EMAIL, notices[0].to)
        self.assertIn("Graphic Designer:", notices[0].body)
        self.assertIn("Assignments", notices[0].body)

    def test_graphic_head_can_change_the_designer_from_assignments(self):
        self.graphic_designer.domain_head_of = "Graphic Designs"
        self.graphic_designer.save()
        request_obj = self._pending_post()
        from engine.confirm import confirm_request

        confirm_request(request_obj)
        task = request_obj.tasks.get(task="Graphic Designer")
        target = OTHER_GRAPHIC_DESIGNER_EMAIL if task.email == GRAPHIC_DESIGNER_EMAIL else GRAPHIC_DESIGNER_EMAIL

        self.client.force_login(User.objects.create_user("gdhead", email=GRAPHIC_DESIGNER_EMAIL))
        response = self.client.post(
            reverse("assignment-reassign", args=[task.pk]), {"member_email": target}
        )
        self.assertRedirects(response, reverse("assignment-detail", args=[request_obj.pk]))
        task.refresh_from_db()
        self.assertEqual(task.email, target)

    # ── task supervisor ──────────────────────────────────────────────────────

    def _coverage(self, **overrides):
        values = dict(
            type="Coverage",
            event_name="Fest",
            contact_email=COMMITTEE_EMAIL,
            venue="Auditorium",
            roles_needed=["Photographer"],
            platforms=["Instagram"],
            status=RequestStatus.NEW,
            event_start=timezone.now() + timedelta(days=5),
            event_end=timezone.now() + timedelta(days=5, hours=2),
        )
        values.update(overrides)
        request_obj = Request.objects.create(**values)
        from engine.workflow import process_new_request

        process_new_request(request_obj)
        request_obj.refresh_from_db()
        return request_obj

    def test_coverage_gets_a_second_year_supervisor(self):
        request_obj = self._coverage()
        supervision = request_obj.tasks.get(task="Task Supervisor")
        self.assertIn(supervision.email, {SUPERVISOR_EMAIL, OTHER_SUPERVISOR_EMAIL})
        self.assertEqual(request_obj.supervisor_email, supervision.email)
        self.assertEqual(supervision.points, 0)
        self.assertIsNone(supervision.deadline)

    def test_second_years_are_never_given_ordinary_work(self):
        request_obj = self._coverage()
        second_years = {SUPERVISOR_EMAIL, OTHER_SUPERVISOR_EMAIL}
        for task in request_obj.tasks.exclude(task="Task Supervisor"):
            self.assertNotIn(task.email, second_years, task.task)

    def test_a_second_year_cannot_be_hand_picked_for_ordinary_work(self):
        request_obj = self._coverage()
        task = request_obj.tasks.get(task="Photographer")
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("assignment-reassign", args=[task.pk]), {"member_email": SUPERVISOR_EMAIL}
        )
        self.assertRedirects(response, reverse("assignment-detail", args=[request_obj.pk]))
        task.refresh_from_db()
        self.assertNotEqual(task.email, SUPERVISOR_EMAIL)

    def test_supervisor_can_only_be_changed_by_staff(self):
        request_obj = self._coverage()
        supervision = request_obj.tasks.get(task="Task Supervisor")
        other = OTHER_SUPERVISOR_EMAIL if supervision.email == SUPERVISOR_EMAIL else SUPERVISOR_EMAIL
        coordinator = request_obj.tasks.get(task="Event Coordinator")
        coordinator_user = User.objects.get(email=coordinator.email) if User.objects.filter(email=coordinator.email).exists() else User.objects.create_user("coord", email=coordinator.email)

        # The event coordinator — who may reassign everything else on the
        # request — is refused for the supervisor.
        self.client.force_login(coordinator_user)
        refused = self.client.post(
            reverse("assignment-reassign", args=[supervision.pk]), {"member_email": other}
        )
        self.assertEqual(refused.status_code, 403)
        # ...and so is a domain head.
        self.client.force_login(self.member_user)  # Asha, head of Photography
        self.assertEqual(
            self.client.post(
                reverse("assignment-reassign", args=[supervision.pk]), {"member_email": other}
            ).status_code,
            403,
        )
        supervision.refresh_from_db()
        self.assertNotEqual(supervision.email, other)

        # The POC can.
        self.client.force_login(self.secretary_user)
        allowed = self.client.post(
            reverse("assignment-reassign", args=[supervision.pk]), {"member_email": other}
        )
        self.assertRedirects(allowed, reverse("assignment-detail", args=[request_obj.pk]))
        supervision.refresh_from_db()
        request_obj.refresh_from_db()
        self.assertEqual(supervision.email, other)
        self.assertEqual(request_obj.supervisor_email, other)

    def test_supervisor_has_no_mark_done_and_cannot_complete_it(self):
        request_obj = self._coverage()
        supervision = request_obj.tasks.get(task="Task Supervisor")
        user = self.supervisor_user if supervision.email == SUPERVISOR_EMAIL else self.other_supervisor_user
        self.client.force_login(user)

        listing = self.client.get(reverse("task-list"))
        self.assertContains(listing, "Task Supervisor")
        self.assertContains(listing, "Supervising")
        self.assertNotContains(listing, reverse("task-complete", args=[supervision.pk]))

        # A direct POST is refused server-side too.
        self.client.post(reverse("task-complete", args=[supervision.pk]))
        supervision.refresh_from_db()
        self.assertNotEqual(supervision.status, TaskStatus.DONE)

    def test_supervisor_can_read_their_request_but_not_reassign_anything(self):
        request_obj = self._coverage()
        supervision = request_obj.tasks.get(task="Task Supervisor")
        user = self.supervisor_user if supervision.email == SUPERVISOR_EMAIL else self.other_supervisor_user
        self.client.force_login(user)
        self.assertEqual(self.client.get(reverse("request-detail", args=[request_obj.pk])).status_code, 200)
        self.assertEqual(self.client.get(reverse("assignment-detail", args=[request_obj.pk])).status_code, 200)
        photographer = request_obj.tasks.get(task="Photographer")
        self.assertEqual(
            self.client.post(
                reverse("assignment-reassign", args=[photographer.pk]), {"member_email": ""}
            ).status_code,
            403,
        )

    def test_a_second_year_who_supervises_nothing_has_no_assignments_access(self):
        self.client.force_login(self.supervisor_user)
        self.assertEqual(self.client.get(reverse("assignment-list")).status_code, 403)

    def test_event_coordinator_finishing_closes_the_supervisor_task(self):
        request_obj = self._coverage()  # far enough out to be accepted outright
        # ...then move it into the past, as if the event had now happened.
        past_start, past_end = timezone.now() - timedelta(hours=3), timezone.now() - timedelta(hours=1)
        request_obj.tasks.update(event_start=past_start, event_end=past_end)
        coordinator = request_obj.tasks.get(task="Event Coordinator")
        supervision = request_obj.tasks.get(task="Task Supervisor")
        user = User.objects.create_user("coord2", email=coordinator.email)
        self.client.force_login(user)
        self.client.post(
            reverse("task-complete", args=[coordinator.pk]), {"content_links": "https://drive.example/x"}
        )
        supervision.refresh_from_db()
        self.assertEqual(supervision.status, TaskStatus.DONE)
        self.assertEqual(supervision.points, 0)

    def test_approval_page_offers_a_supervisor_picker_for_coverage(self):
        request_obj = self._coverage(
            event_start=timezone.now() + timedelta(hours=3), event_end=timezone.now() + timedelta(hours=4)
        )
        self.assertEqual(request_obj.status, RequestStatus.PENDING)
        self.client.force_login(self.secretary_user)
        response = self.client.get(reverse("approval-detail", args=[request_obj.pk]))
        self.assertContains(response, "Task Supervisor (a 2nd-year)")
        self.assertContains(response, SUPERVISOR_EMAIL)
        self.assertContains(response, OTHER_SUPERVISOR_EMAIL)
        # Only second-years are on offer.
        self.assertNotContains(response, MEMBER_EMAIL + '"')

    def test_poc_can_change_the_supervisor_while_approving(self):
        request_obj = self._coverage(
            event_start=timezone.now() + timedelta(hours=3), event_end=timezone.now() + timedelta(hours=4)
        )
        suggested = request_obj.tasks.get(task="Task Supervisor").email
        other = OTHER_SUPERVISOR_EMAIL if suggested == SUPERVISOR_EMAIL else SUPERVISOR_EMAIL

        self.client.force_login(self.secretary_user)
        self.client.post(
            reverse("approval-decide", args=[request_obj.pk]), {"decision": "approve", "member_email": other}
        )
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatus.ACCEPTED)
        self.assertEqual(request_obj.tasks.get(task="Task Supervisor").email, other)
        self.assertEqual(request_obj.supervisor_email, other)

    def test_poc_cannot_pick_a_first_year_as_supervisor(self):
        request_obj = self._coverage(
            event_start=timezone.now() + timedelta(hours=3), event_end=timezone.now() + timedelta(hours=4)
        )
        self.client.force_login(self.secretary_user)
        self.client.post(
            reverse("approval-decide", args=[request_obj.pk]),
            {"decision": "approve", "member_email": MEMBER_EMAIL},
        )
        request_obj.refresh_from_db()
        self.assertEqual(request_obj.status, RequestStatus.PENDING)  # refused; still waiting

    def test_late_notice_reaches_the_supervisor_and_adds_no_strike(self):
        from engine.workflow import run_deadline_check

        request_obj = self._coverage()
        request_obj.tasks.filter(task="Photo Editor").update(deadline=timezone.now() - timedelta(hours=1))
        editor = request_obj.tasks.get(task="Photo Editor")
        strikes_before = (self.member.yellow_strikes, self.member.red_strikes)
        mail.outbox.clear()

        run_deadline_check()

        editor.refresh_from_db()
        self.assertEqual(editor.status, TaskStatus.LATE)
        late = [m for m in mail.outbox if "[Late]" in m.subject]
        self.assertEqual(len(late), 1)
        self.assertIn(request_obj.supervisor_email, late[0].to)
        self.assertIn(request_obj.coordinator_email, late[0].to)
        self.assertIn(editor.email, late[0].to)
        self.member.refresh_from_db()
        self.assertEqual((self.member.yellow_strikes, self.member.red_strikes), strikes_before)

    def test_approvals_forbidden_for_plain_user(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("approval-list"))
        self.assertEqual(response.status_code, 403)

    # ── dashboard ────────────────────────────────────────────────────────────

    def test_dashboard_renders_with_filters(self):
        self.client.force_login(self.admin_user)
        response = self.client.get(reverse("dashboard"), {"period": "30d", "campus": "MBA Campus"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Leaderboard")

    # ── admin ────────────────────────────────────────────────────────────────

    def test_portal_admin_renders(self):
        self.client.force_login(self.admin_user)
        # The page shows one group at a time: the roster on Team, clubs on Committees.
        self.assertContains(self.client.get(reverse("portal-admin"), {"tab": "team"}), "Asha")
        self.assertContains(self.client.get(reverse("portal-admin"), {"tab": "committees"}), "Sapient")

    def test_admin_can_update_a_members_contact_number(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(
            reverse("set-member-phone"), {"member_email": MEMBER_EMAIL, "phone": "9123456780"}
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.phone, "9123456780")

    def test_domain_head_cannot_update_contact_numbers(self):
        # Master-roster contact editing is secretary/admin only, unlike a
        # member's own profile self-edit.
        self.client.force_login(self.member_user)  # Asha, a domain head, not staff
        response = self.client.post(
            reverse("set-member-phone"), {"member_email": SAME_VERTICAL_EMAIL, "phone": "9123456780"}
        )
        self.assertEqual(response.status_code, 403)

    def test_committee_manage_add_and_update(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("committee-manage"),
            {"email": "newclub@iimsirmaur.ac.in", "name": "New Club", "acronym": "NEW", "type": "Club", "campus": "MBA Campus"},
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.assertTrue(Committee.objects.filter(email="newclub@iimsirmaur.ac.in").exists())

    def test_committee_manage_updates_an_existing_committee_by_email(self):
        Committee.objects.filter(pk=self.committee.pk).update(last_seq=7)
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("committee-manage"),
            {"email": COMMITTEE_EMAIL.upper(), "name": "Renamed", "acronym": "SPT", "type": "Club", "campus": "BMS Campus"},
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.committee.refresh_from_db()
        self.assertEqual(self.committee.name, "Renamed")
        self.assertEqual(self.committee.campus, "BMS Campus")
        self.assertEqual(self.committee.last_seq, 7, "the reference counter must survive an update")
        self.assertEqual(Committee.objects.filter(email=COMMITTEE_EMAIL).count(), 1)

    def test_partial_team_import_leaves_columns_it_does_not_have_alone(self):
        self.client.force_login(self.secretary_user)
        TeamMember.objects.filter(pk=self.member.pk).update(phone="9000000000")
        csv_text = f"name,email\nAsha Renamed,{MEMBER_EMAIL}\nSup Renamed,{SUPERVISOR_EMAIL}\n"
        self.client.post(reverse("team-import"), {"csv_text": csv_text})
        self.member.refresh_from_db()
        self.supervisor.refresh_from_db()
        self.assertEqual(self.member.name, "Asha Renamed")
        self.assertEqual(self.member.domain_head_of, "Photography")
        self.assertEqual(self.member.phone, "9000000000")
        self.assertEqual(self.member.skills, ["Photography", "Photo Editing"])
        self.assertEqual(self.supervisor.year, 2, "a second-year must not be reset to year 1")

    def test_team_import(self):
        self.client.force_login(self.secretary_user)
        csv_text = "name,email,vertical,year,skills,campus,phone,active\nRahul,rahul@iimsirmaur.ac.in,Photography,1,Photography,MBA Campus,,true\n"
        response = self.client.post(reverse("team-import"), {"csv_text": csv_text})
        self.assertRedirects(response, reverse("portal-admin"))
        self.assertTrue(TeamMember.objects.filter(email="rahul@iimsirmaur.ac.in").exists())

    def test_set_vertical_head(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("vertical-head"), {"member_email": MEMBER_EMAIL, "vertical": "Photography"}
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.domain_head_of, "Photography")

    def test_set_availability(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("set-availability"), {"member_email": MEMBER_EMAIL, "availability": "out"}
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.member.refresh_from_db()
        self.assertEqual(self.member.availability, "out")

    def test_point_scheme_update_requires_admin(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(reverse("point-scheme"), {"coordinator_points": "25"})
        self.assertEqual(response.status_code, 403)

    def test_point_scheme_update_as_admin(self):
        self.client.force_login(self.admin_user)
        payload = {
            "coordinator_points": "25",
            "domain_task_points": "10",
            "vetter_points": "10",
            "early_window_hours": "24",
            "early_bonus_pct": "30",
            "late_threshold_hours": "48",
            "late_penalty_pct": "30",
            "subsequent_delay_hours": "6",
            "subsequent_penalty_pct": "10",
        }
        response = self.client.post(reverse("point-scheme"), payload)
        self.assertRedirects(response, reverse("portal-admin"))
        self.assertEqual(PointsScheme.load().coordinator_points, 25)

    def test_portal_admin_forbidden_for_plain_user(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("portal-admin"))
        self.assertEqual(response.status_code, 403)
