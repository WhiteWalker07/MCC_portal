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


class PortalViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        for task, skill, points, sla, at_event, requestable, internal, vertical in [
            ("Photographer", "Photography", 5, 0, True, True, True, "Photography"),
            ("Photo Editor", "Photo Editing", 3, 24, False, False, True, "Photography"),
            ("Vetter", "Vetting", 2, 24, False, False, True, ""),
            ("Event Coordinator", "Coordination", 4, 0, True, False, True, ""),
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
            year=2,
            vertical="Photography",
            domain_head_of="Photography",
            skills=["Photography", "Photo Editing", "Coordination", "Vetting"],
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

        cls.admin_user = User.objects.create_user("admin", email=ADMIN_EMAIL)
        cls.secretary_user = User.objects.create_user("secretary", email=SECRETARY_EMAIL)
        cls.committee_user = User.objects.create_user("committee", email=COMMITTEE_EMAIL)
        cls.member_user = User.objects.create_user("member", email=MEMBER_EMAIL)
        cls.plain_user = User.objects.create_user("plain", email=PLAIN_EMAIL)

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
        self.assertEqual(request_obj.status, RequestStatus.ACCEPTED)
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
        task = request_obj.tasks.get(task="Vetter")
        # Force Asha onto it directly so the completion path is under test,
        # independent of who the engine happened to auto-pick.
        task.email = MEMBER_EMAIL
        task.member = "Asha"
        task.status = TaskStatus.CONFIRMED
        task.save()

        self.client.force_login(self.member_user)
        list_response = self.client.get(reverse("task-list"))
        self.assertContains(list_response, "Vetter")

        complete_response = self.client.post(reverse("task-complete", args=[task.pk]))
        self.assertRedirects(complete_response, reverse("task-list"))
        task.refresh_from_db()
        self.assertEqual(task.status, TaskStatus.DONE)

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
        self.assertEqual(task.email, MEMBER_EMAIL)  # the only one with "Photography"

    def test_assignment_list_forbidden_without_a_role(self):
        self.client.force_login(self.plain_user)
        response = self.client.get(reverse("assignment-list"))
        self.assertEqual(response.status_code, 403)

    # ── strikes ──────────────────────────────────────────────────────────────

    def test_secretary_can_strike_any_member(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "reason": "missed two deadlines"}
        )
        self.assertRedirects(response, reverse("assignment-list"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.strikes, 1)

    def test_admin_can_strike_any_member(self):
        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL})
        self.assertRedirects(response, reverse("assignment-list"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.strikes, 1)

    def test_domain_head_can_strike_within_their_own_vertical(self):
        self.client.force_login(self.member_user)  # Asha, head of Photography
        response = self.client.post(reverse("issue-strike"), {"member_email": SAME_VERTICAL_EMAIL})
        self.assertRedirects(response, reverse("assignment-list"))
        self.same_vertical_member.refresh_from_db()
        self.assertEqual(self.same_vertical_member.strikes, 1)

    def test_domain_head_cannot_strike_a_different_vertical(self):
        self.client.force_login(self.member_user)  # Asha, head of Photography
        response = self.client.post(reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL})
        # Not in Asha's dropdown at all, so the form itself rejects the choice.
        self.assertRedirects(response, reverse("assignment-list"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.strikes, 0)

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

        response = self.client.post(reverse("issue-strike"), {"member_email": OTHER_VERTICAL_EMAIL})
        self.assertEqual(response.status_code, 302)  # invalid form -> redirected, not applied
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.strikes, 0)

    # ── remove strike / remove from team (secretary + admin only) ──────────────

    def test_secretary_can_remove_a_strike(self):
        self.other_vertical_member.strikes = 2
        self.other_vertical_member.save()

        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("remove-strike"), {"member_email": OTHER_VERTICAL_EMAIL, "reason": "issued in error"}
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.strikes, 1)

    def test_admin_can_remove_a_strike(self):
        self.other_vertical_member.strikes = 1
        self.other_vertical_member.save()

        self.client.force_login(self.admin_user)
        response = self.client.post(reverse("remove-strike"), {"member_email": OTHER_VERTICAL_EMAIL})
        self.assertRedirects(response, reverse("portal-admin"))
        self.other_vertical_member.refresh_from_db()
        self.assertEqual(self.other_vertical_member.strikes, 0)

    def test_domain_head_cannot_remove_a_strike(self):
        # Narrower than issuing one: removal is secretary/admin only, no
        # domain-head carve-out, even within their own vertical.
        self.same_vertical_member.strikes = 1
        self.same_vertical_member.save()

        self.client.force_login(self.member_user)  # Asha, head of Photography
        response = self.client.post(reverse("remove-strike"), {"member_email": SAME_VERTICAL_EMAIL})
        self.assertEqual(response.status_code, 403)
        self.same_vertical_member.refresh_from_db()
        self.assertEqual(self.same_vertical_member.strikes, 1)

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
        self.member.strikes = 2
        self.member.save()

        self.client.force_login(self.admin_user)
        self.client.post(reverse("remove-from-team"), {"member_email": MEMBER_EMAIL})
        self.member.refresh_from_db()
        self.assertFalse(self.member.active)
        self.assertEqual(self.member.points, 50)
        self.assertEqual(self.member.strikes, 2)

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
        response = self.client.get(reverse("portal-admin"))
        self.assertContains(response, "Sapient")
        self.assertContains(response, "Asha")

    def test_committee_manage_add_and_update(self):
        self.client.force_login(self.secretary_user)
        response = self.client.post(
            reverse("committee-manage"),
            {"email": "newclub@iimsirmaur.ac.in", "name": "New Club", "acronym": "NEW", "type": "Club", "campus": "MBA Campus"},
        )
        self.assertRedirects(response, reverse("portal-admin"))
        self.assertTrue(Committee.objects.filter(email="newclub@iimsirmaur.ac.in").exists())

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
