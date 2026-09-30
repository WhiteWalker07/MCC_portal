"""
Views for team meetings, and for adding an extra photographer/videographer to a
Coverage request (who now brings their own editing task).

Django's test client + locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import Attendance, Availability
from core.models import Meeting, Request, TeamMember
from engine.tests.factories import ASHA, COMMITTEE_EMAIL, NEHA, SUPERVISOR, build_world
from engine.workflow import process_new_request

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
RAVI = "ravi@iimsirmaur.ac.in"
OLI = "oli@iimsirmaur.ac.in"


def fmt(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


class MeetingViewBase(TestCase):
    def setUp(self):
        build_world()
        # Asha is the fixture's Photography head; Neha is an ordinary first-year.
        TeamMember.objects.create(email=RAVI, name="Ravi", year=1, campus="Permanent", vertical="Photography")
        TeamMember.objects.create(
            email=OLI, name="Oli", year=1, campus="Permanent", vertical="Photography",
            availability=Availability.OUT,
        )
        self.poc = User.objects.create_user("poc", email=POC)
        self.head = User.objects.create_user("asha", email=ASHA)
        self.neha = User.objects.create_user("neha", email=NEHA)
        self.ravi = User.objects.create_user("ravi", email=RAVI)
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.start = timezone.now() + timedelta(days=2)
        self.end = self.start + timedelta(hours=1)
        mail.outbox.clear()

    def form(self, **extra):
        body = {
            "title": "Weekly sync", "start": fmt(self.start), "end": fmt(self.end),
            "venue": "Room 4", "agenda": "Roster", "invite_mode": "people", "people": [NEHA, RAVI],
        }
        body.update(extra)
        return body

    def call_meeting(self, user=None, **extra):
        self.client.force_login(user or self.poc)
        response = self.client.post(reverse("meeting-new"), self.form(**extra))
        return response, Meeting.objects.order_by("-pk").first()


class CallingAMeetingTests(MeetingViewBase):
    def test_poc_and_admin_and_a_vertical_head_can_call_one(self):
        for user in (self.poc, self.head):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("meeting-new")).status_code, 200, user.email)

    def test_an_ordinary_member_and_a_club_cannot(self):
        for user in (self.neha, self.club):
            self.client.force_login(user)
            self.assertEqual(self.client.get(reverse("meeting-new")).status_code, 403, user.email)
            self.assertEqual(self.client.post(reverse("meeting-new"), self.form()).status_code, 403)
        self.assertEqual(Meeting.objects.count(), 0)

    def test_a_head_can_invite_anyone_not_just_their_own_vertical(self):
        response, meeting = self.call_meeting(self.head, people=[NEHA])  # Neha: Graphic Designs
        self.assertRedirects(response, reverse("meeting-detail", args=[meeting.pk]))
        self.assertEqual(meeting.called_by, ASHA)
        self.assertEqual([i.member.email for i in meeting.invites.all()], [NEHA])

    def test_invitations_are_emailed(self):
        self.call_meeting()
        invite = next(m for m in mail.outbox if m.subject.startswith("[Meeting]"))
        self.assertEqual(set(invite.to), {NEHA, RAVI})

    def test_someone_out_of_work_is_not_offered_and_is_refused(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("meeting-new"))
        self.assertNotContains(page, "Oli")
        response, meeting = self.call_meeting(people=[OLI])
        self.assertEqual(response.status_code, 200)  # form redisplayed with an error
        self.assertIsNone(meeting)
        self.assertEqual(mail.outbox, [])

    def test_whole_team_mode_skips_people_out_of_work(self):
        _, meeting = self.call_meeting(invite_mode="all", people=[])
        invited = set(meeting.invites.values_list("member__email", flat=True))
        self.assertNotIn(OLI, invited)
        self.assertEqual(invited, {ASHA, NEHA, SUPERVISOR, RAVI})

    def test_vertical_mode_uses_primary_or_secondary(self):
        TeamMember.objects.filter(email=NEHA).update(secondary_vertical="Photography")
        _, meeting = self.call_meeting(invite_mode="verticals", verticals=["Photography"], people=[])
        invited = set(meeting.invites.values_list("member__email", flat=True))
        self.assertEqual(invited, {ASHA, RAVI, NEHA})

    def test_a_mode_needs_something_picked(self):
        for extra in ({"invite_mode": "verticals", "people": []}, {"invite_mode": "people", "people": []}):
            response, meeting = self.call_meeting(**extra)
            self.assertEqual(response.status_code, 200)
            self.assertIsNone(meeting)

    def test_a_meeting_in_the_past_is_refused(self):
        response, meeting = self.call_meeting(
            start=fmt(timezone.now() - timedelta(days=1)), end=fmt(timezone.now() - timedelta(hours=23))
        )
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(meeting)

    def test_ticking_mom_assigns_a_first_year_and_tells_them(self):
        _, meeting = self.call_meeting(invite_mode="all", people=[], wants_mom="on", mom_email="")
        self.assertTrue(meeting.wants_mom)
        self.assertNotEqual(meeting.mom_email, SUPERVISOR)  # second-years are never picked
        self.assertNotEqual(meeting.mom_email, "")
        duty = next(m for m in mail.outbox if m.subject.startswith("[MOM duty]"))
        self.assertEqual(duty.to, [meeting.mom_email])

    def test_the_caller_can_name_the_minutes_taker(self):
        _, meeting = self.call_meeting(wants_mom="on", mom_email=RAVI)
        self.assertEqual(meeting.mom_email, RAVI)

    def test_no_mom_when_the_box_is_unticked(self):
        _, meeting = self.call_meeting(mom_email=RAVI)
        self.assertFalse(meeting.wants_mom)
        self.assertEqual(meeting.mom_email, "")


class MeetingPagesTests(MeetingViewBase):
    def setUp(self):
        super().setUp()
        _, self.meeting = self.call_meeting(self.head)  # called by Asha, inviting Neha and Ravi
        mail.outbox.clear()
        self.url = reverse("meeting-detail", args=[self.meeting.pk])

    def test_the_invited_can_see_it_and_a_stranger_cannot(self):
        self.client.force_login(self.neha)
        self.assertContains(self.client.get(self.url), "Weekly sync")
        self.client.force_login(self.club)
        self.assertEqual(self.client.get(self.url).status_code, 403)

    def test_the_list_shows_my_meetings_only(self):
        self.client.force_login(self.neha)
        self.assertContains(self.client.get(reverse("meeting-list")), "Weekly sync")
        other = TeamMember.objects.get(email=SUPERVISOR)
        User.objects.create_user("sup", email=other.email)
        self.client.force_login(User.objects.get(email=other.email))
        self.assertNotContains(self.client.get(reverse("meeting-list")), "Weekly sync")

    def test_the_menu_link_shows_for_team_and_staff_but_not_clubs(self):
        for user, shown in ((self.neha, True), (self.poc, True), (self.club, False)):
            self.client.force_login(user)
            page = self.client.get(reverse("request-list"))
            self.assertEqual(reverse("meeting-list") in page.content.decode(), shown, user.email)

    def test_only_the_caller_and_poc_see_the_controls(self):
        self.client.force_login(self.neha)
        self.assertNotContains(self.client.get(self.url), "Cancel meeting")
        for user in (self.head, self.poc):
            self.client.force_login(user)
            self.assertContains(self.client.get(self.url), "Cancel meeting", msg_prefix=user.email)

    def test_editing_is_for_the_caller_or_poc_only(self):
        edit = reverse("meeting-edit", args=[self.meeting.pk])
        self.client.force_login(self.neha)
        self.assertEqual(self.client.get(edit).status_code, 403)
        self.client.force_login(self.head)
        response = self.client.post(edit, self.form(venue="Room 9"))
        self.assertRedirects(response, self.url)
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.venue, "Room 9")
        self.assertTrue(any(m.subject.startswith("[Meeting updated]") for m in mail.outbox))

    def test_cancelling(self):
        cancel = reverse("meeting-cancel", args=[self.meeting.pk])
        self.client.force_login(self.neha)
        self.assertEqual(self.client.post(cancel).status_code, 403)
        self.client.force_login(self.poc)
        self.client.post(cancel)
        self.meeting.refresh_from_db()
        self.assertTrue(self.meeting.is_cancelled)
        self.assertTrue(any(m.subject.startswith("[Meeting cancelled]") for m in mail.outbox))

    def test_changing_the_minutes_taker(self):
        url = reverse("meeting-set-mom", args=[self.meeting.pk])
        self.client.force_login(self.neha)
        self.assertEqual(self.client.post(url, {"mom_email": NEHA}).status_code, 403)
        self.client.force_login(self.head)
        self.client.post(url, {"mom_email": RAVI})
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.mom_email, RAVI)
        self.client.post(url, {"mom_email": ASHA})  # not among the invited first-years
        self.meeting.refresh_from_db()
        self.assertEqual(self.meeting.mom_email, RAVI)


class AttendanceViewTests(MeetingViewBase):
    def setUp(self):
        super().setUp()
        _, self.meeting = self.call_meeting(self.head)
        self.invite = self.meeting.invites.get(member__email=NEHA)
        self.url = reverse("meeting-attendance", args=[self.meeting.pk])
        mail.outbox.clear()

    def start_now(self):
        Meeting.objects.filter(pk=self.meeting.pk).update(
            start=timezone.now() - timedelta(minutes=10), end=timezone.now() + timedelta(minutes=50)
        )

    def post_status(self, user, status):
        self.client.force_login(user)
        return self.client.post(self.url, {f"status_{self.invite.pk}": status})

    def strikes(self):
        return TeamMember.objects.get(email=NEHA).yellow_strikes

    def test_not_available_before_the_meeting_starts(self):
        self.post_status(self.head, Attendance.ABSENT)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.attendance, "")
        self.assertEqual(self.strikes(), 0)
        page = self.client.get(reverse("meeting-detail", args=[self.meeting.pk]))
        self.assertNotContains(page, "Save attendance")

    def test_the_caller_marks_absent_and_the_strike_is_automatic(self):
        self.start_now()
        page = self.client.force_login(self.head) or self.client.get(reverse("meeting-detail", args=[self.meeting.pk]))
        self.assertContains(page, "Save attendance")
        self.post_status(self.head, Attendance.ABSENT)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.attendance, Attendance.ABSENT)
        self.assertEqual(self.strikes(), 1)

    def test_poc_can_mark_it_too(self):
        self.start_now()
        self.post_status(self.poc, Attendance.ABSENT)
        self.assertEqual(self.strikes(), 1)

    def test_an_invitee_cannot_mark_their_own(self):
        self.start_now()
        self.assertEqual(self.post_status(self.neha, Attendance.PRESENT).status_code, 403)
        self.invite.refresh_from_db()
        self.assertEqual(self.invite.attendance, "")

    def test_correcting_absent_removes_the_strike(self):
        self.start_now()
        self.post_status(self.head, Attendance.ABSENT)
        self.post_status(self.head, Attendance.EXCUSED)
        self.assertEqual(self.strikes(), 0)

    def test_the_marks_are_shown_to_the_caller_but_the_form_is_not_shown_to_invitees(self):
        self.start_now()
        self.post_status(self.head, Attendance.ABSENT)
        self.client.force_login(self.neha)
        page = self.client.get(reverse("meeting-detail", args=[self.meeting.pk]))
        self.assertNotContains(page, "Save attendance")


# ── Adding an extra shooter brings their editing task ───────────────────────


class ExtraShooterTests(TestCase):
    def setUp(self):
        build_world()
        TeamMember.objects.create(
            email=RAVI, name="Ravi", year=1, campus="Permanent", vertical="Photography", skills=["Photography"]
        )
        self.poc = User.objects.create_user("poc", email=POC)
        start = timezone.now() + timedelta(days=5)
        self.request = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL, venue="Hall",
            roles_needed=[], platforms=[], status="New",
            event_start=start, event_end=start + timedelta(hours=2),
        )
        process_new_request(self.request)
        self.request.refresh_from_db()
        mail.outbox.clear()

    def add(self, task_type, email):
        self.client.force_login(self.poc)
        return self.client.post(
            reverse("assignment-add", args=[self.request.pk]), {"task_type": task_type, "member_email": email}
        )

    def test_an_extra_photographer_arrives_with_their_own_photo_editing(self):
        self.add("Photographer", RAVI)
        photographer = self.request.tasks.get(task="Photographer")
        editor = self.request.tasks.get(task="Photo Editor")
        self.assertEqual((photographer.email, editor.email), (RAVI, RAVI))
        self.assertEqual(editor.paired_task_id, photographer.pk)
        self.assertEqual(editor.deadline, self.request.event_end + timedelta(hours=24))

    def test_the_new_shooter_is_told_about_both_tasks(self):
        self.add("Photographer", RAVI)
        assigned = [m for m in mail.outbox if m.subject.startswith("[Assigned]") and RAVI in m.to]
        self.assertEqual(len(assigned), 2)

    def test_the_coordinator_deadline_moves_out_to_follow_the_new_task(self):
        before = self.request.tasks.get(task="Event Coordinator").deadline
        self.assertEqual(before, self.request.event_end + timedelta(hours=12))
        self.add("Photographer", RAVI)
        after = self.request.tasks.get(task="Event Coordinator").deadline
        self.assertEqual(after, self.request.event_end + timedelta(hours=36))

    def test_the_page_says_so(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("assignment-detail", args=[self.request.pk]))
        self.assertContains(page, "edits their own work")
