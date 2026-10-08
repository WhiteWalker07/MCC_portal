"""
The landing page: sign-in screen when signed out, and a Home whose sections depend on
who you are (club, team member, coordinator/supervisor, vertical head, POC/Admin).

Django's test client + locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import Availability
from core.models import LeaveRequest, Meeting, MeetingInvite, Request, Task, TeamMember
from engine.tests.factories import ASHA, COMMITTEE_EMAIL, NEHA, SUPERVISOR, build_world

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
ADMIN = "admin@iimsirmaur.ac.in"


def request_for(email=COMMITTEE_EMAIL, **extra):
    values = dict(type="Coverage", event_name="Fest", contact_email=email, status="Request Accepted", ref_code="MKTG_1")
    values.update(extra)
    return Request.objects.create(**values)


def task_for(request_obj, email, name="Photographer", status="CONFIRMED", due_in=timedelta(days=2), **extra):
    return Task.objects.create(
        request=request_obj, req_type="Coverage", ref_code=request_obj.ref_code, task=name, email=email,
        member=email.split("@")[0], status=status, deadline=timezone.now() + due_in,
        event_name=request_obj.event_name, **extra,
    )


class Base(TestCase):
    def setUp(self):
        build_world()
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.poc = User.objects.create_user("poc", email=POC)
        self.neha = User.objects.create_user("neha", email=NEHA)
        self.asha = User.objects.create_user("asha", email=ASHA)
        self.sup = User.objects.create_user("sup", email=SUPERVISOR)
        self.plain = User.objects.create_user("plain", email="plain@iimsirmaur.ac.in")

    def home(self, user=None):
        self.client.logout()
        if user is not None:
            self.client.force_login(user)
        return self.client.get(reverse("home"))


class LandingTests(Base):
    def test_signed_out_you_see_the_sign_in_screen(self):
        response = self.home()
        self.assertContains(response, "Continue with Google")
        self.assertNotContains(response, "Hello,")

    def test_signed_in_you_land_on_home_not_on_a_redirect(self):
        for user in (self.club, self.neha, self.poc, self.plain):
            response = self.home(user)
            self.assertEqual(response.status_code, 200, user.email)
            self.assertContains(response, "Hello,")
            self.assertNotContains(response, "Continue with Google")

    def test_sign_in_redirects_to_the_site_root(self):
        from django.conf import settings

        self.assertEqual(settings.LOGIN_REDIRECT_URL, "/")

    def test_home_is_first_in_the_menu_for_everyone(self):
        for user in (self.club, self.neha, self.poc, self.plain):
            self.client.force_login(user)
            items = self.client.get(reverse("request-list")).context["nav_items"]
            self.assertEqual(items[0].url_name, "home", user.email)

    def test_home_is_marked_current_on_the_home_page(self):
        self.client.force_login(self.neha)
        self.assertContains(self.client.get(reverse("home")), 'class="nav__link nav__link--active" href="/"')


class EveryoneSeesTests(Base):
    def test_a_new_request_button_and_their_own_requests_only(self):
        mine = request_for(event_name="Mine")
        request_for(email="someone@iimsirmaur.ac.in", event_name="Not mine")
        page = self.home(self.club)
        self.assertContains(page, reverse("request-new"))
        self.assertContains(page, "Mine")
        self.assertNotContains(page, "Not mine")
        self.assertContains(page, reverse("request-detail", args=[mine.pk]))

    def test_someone_with_no_requests_is_invited_to_raise_one(self):
        self.assertContains(self.home(self.plain), "haven't raised a request yet")

    def test_the_list_is_capped_with_a_link_to_the_rest(self):
        for i in range(8):
            request_for(event_name=f"E{i}", ref_code=f"MKTG_{i + 1}")
        page = self.home(self.club)
        self.assertEqual(len(page.context["my_requests"]), 5)
        self.assertContains(page, reverse("request-list"))


class ClubSeesNoStaffOrTeamSectionsTests(Base):
    def test_a_club_has_no_task_meeting_or_attention_sections(self):
        page = self.home(self.club)
        for text in ("My tasks", "Upcoming meetings", "Needs attention", "Events you look after"):
            self.assertNotContains(page, text)

    def test_a_plain_account_neither(self):
        page = self.home(self.plain)
        for text in ("My tasks", "Needs attention"):
            self.assertNotContains(page, text)


class TeamMemberSeesTests(Base):
    def test_their_open_tasks_overdue_first_then_soonest_due(self):
        r = request_for()
        task_for(r, NEHA, "Graphic Designer", "CONFIRMED", due_in=timedelta(days=5))
        task_for(r, NEHA, "Content Writer", "CONFIRMED", due_in=timedelta(days=1))
        task_for(r, NEHA, "Photographer", "LATE", due_in=-timedelta(days=1))
        page = self.home(self.neha)
        self.assertEqual([t.task for t in page.context["my_tasks"]], ["Photographer", "Content Writer", "Graphic Designer"])
        self.assertEqual((page.context["my_task_count"], page.context["my_late_count"]), (3, 1))
        self.assertContains(page, "1 overdue")

    def test_finished_unconfirmed_other_peoples_and_supervision_tasks_are_left_out(self):
        r = request_for()
        task_for(r, NEHA, "Photographer", "DONE")
        task_for(r, NEHA, "Content Writer", "PROPOSED")
        task_for(r, NEHA, "Task Supervisor", "CONFIRMED")
        task_for(r, ASHA, "Photo Editor", "CONFIRMED")
        self.assertEqual(self.home(self.neha).context["my_task_count"], 0)

    def test_a_sub_event_task_is_named_with_its_sub_event(self):
        from core.models import SubEvent

        r = request_for(is_multiday=True)
        sub = SubEvent.objects.create(request=r, name="Opening", start=timezone.now() + timedelta(days=3),
                                      end=timezone.now() + timedelta(days=3, hours=2))
        task_for(r, NEHA, "Photographer", sub_event=sub)
        self.assertContains(self.home(self.neha), "Photographer — Opening")

    def test_nothing_open_says_so(self):
        self.assertContains(self.home(self.neha), "all caught up")

    def test_points_strikes_and_a_link_to_all_tasks(self):
        TeamMember.objects.filter(email=NEHA).update(points=33, yellow_strikes=2, red_strikes=1)
        page = self.home(self.neha)
        self.assertContains(page, "33 pts")
        self.assertContains(page, "2 yellow")
        self.assertContains(page, reverse("task-list"))

    def test_out_of_work_and_a_waiting_request_are_shown(self):
        member = TeamMember.objects.get(email=NEHA)
        LeaveRequest.objects.create(member=member, reason="Exams", start_date=timezone.localdate(), end_date=timezone.localdate())
        self.assertContains(self.home(self.neha), "waiting for the POC")
        TeamMember.objects.filter(email=NEHA).update(availability=Availability.OUT)
        self.assertContains(self.home(self.neha), "You're marked out of work")

    def test_upcoming_meetings_only_ones_they_are_invited_to_not_cancelled_not_past(self):
        member = TeamMember.objects.get(email=NEHA)
        now = timezone.now()

        def meeting(title, start, **kw):
            m = Meeting.objects.create(title=title, start=start, end=start + timedelta(hours=1), called_by=POC, **kw)
            MeetingInvite.objects.create(meeting=m, member=member)
            return m

        meeting("Soon", now + timedelta(days=1))
        meeting("Gone", now - timedelta(days=2))
        meeting("Cancelled", now + timedelta(days=2), cancelled_at=now)
        uninvited = Meeting.objects.create(title="Secret", start=now + timedelta(days=1), end=now + timedelta(days=1, hours=1), called_by=POC)
        page = self.home(self.neha)
        self.assertEqual([m.title for m in page.context["my_meetings"]], ["Soon"])
        self.assertNotContains(page, "Secret")
        self.assertContains(page, reverse("meeting-detail", args=[page.context["my_meetings"][0].pk]))
        self.assertTrue(uninvited.pk)

    def test_only_their_own_tasks_never_someone_elses(self):
        r = request_for()
        task_for(r, ASHA, "Photographer", "CONFIRMED")
        self.assertEqual(self.home(self.neha).context["my_task_count"], 0)


class CoordinatingAndSupervisingTests(Base):
    def test_requests_you_coordinate_and_supervise_while_they_are_running(self):
        coordinating = request_for(event_name="Coord", coordinator_email=NEHA)
        supervising = request_for(event_name="Super", supervisor_email=SUPERVISOR, ref_code="MKTG_2")
        request_for(event_name="Finished", coordinator_email=NEHA, status="Posted", ref_code="MKTG_3")
        page = self.home(self.neha)
        self.assertEqual([r.pk for r in page.context["coordinating"]], [coordinating.pk])
        self.assertContains(page, "you coordinate")
        self.assertContains(self.home(self.sup), "you supervise")
        self.assertEqual([r.pk for r in self.home(self.sup).context["supervising"]], [supervising.pk])

    def test_a_vertical_head_sees_roles_nobody_took_in_their_vertical(self):
        r = request_for()
        Task.objects.create(request=r, req_type="Coverage", ref_code="MKTG_1", task="Photographer", status="UNFILLED", vertical="Photography")
        Task.objects.create(request=r, req_type="Coverage", ref_code="MKTG_1", task="Graphic Designer", status="UNFILLED", vertical="Graphic Designs")
        page = self.home(self.asha)  # head of Photography
        self.assertEqual(page.context["unfilled_in_vertical"], 1)
        self.assertContains(page, "have nobody assigned")
        self.assertNotIn("unfilled_in_vertical", self.home(self.neha).context)


class StaffSeesTests(Base):
    def test_the_attention_counts(self):
        r = request_for(status="Pending for POC approval")
        member = TeamMember.objects.get(email=NEHA)
        LeaveRequest.objects.create(member=member, reason="x", start_date=timezone.localdate(), end_date=timezone.localdate())
        live = request_for(ref_code="MKTG_9")
        task_for(live, NEHA, "Photographer", "LATE")
        Task.objects.create(request=live, req_type="Coverage", ref_code="MKTG_9", task="Videographer", status="UNFILLED")
        done = request_for(status="Posted", ref_code="MKTG_8")
        task_for(done, NEHA, "Photo Editor", "LATE")  # closed request: not counted
        page = self.home(self.poc)
        self.assertEqual(
            (page.context["pending_requests"], page.context["pending_leaves"], page.context["late_tasks"], page.context["unfilled_tasks"]),
            (1, 1, 1, 1),
        )
        self.assertTrue(r.pk)

    def test_the_tiles_link_to_where_you_act(self):
        page = self.home(self.poc)
        self.assertContains(page, reverse("approval-list"))
        self.assertContains(page, reverse("assignment-list"))

    def test_events_in_the_next_week_soonest_first_and_nothing_beyond_or_rejected(self):
        now = timezone.now()
        soon = request_for(event_name="Soon", event_start=now + timedelta(days=1), event_end=now + timedelta(days=1, hours=2), ref_code="MKTG_5")
        later = request_for(event_name="Later", event_start=now + timedelta(days=5), event_end=now + timedelta(days=5, hours=2), ref_code="MKTG_6")
        request_for(event_name="Far", event_start=now + timedelta(days=30), event_end=now + timedelta(days=30, hours=2), ref_code="MKTG_7")
        request_for(event_name="Refused", event_start=now + timedelta(days=2), event_end=now + timedelta(days=2, hours=2),
                    status="Rejected", ref_code="MKTG_4")
        page = self.home(self.poc)
        self.assertEqual([r.pk for r in page.context["upcoming_events"]], [soon.pk, later.pk])
        self.assertNotContains(page, "Far")

    def test_nothing_scheduled_says_so(self):
        self.assertContains(self.home(self.poc), "Nothing is scheduled in the next 7 days")

    def test_admin_sees_the_same_as_the_poc(self):
        admin = User.objects.create_user("admin", email=ADMIN)
        self.assertContains(self.home(admin), "Needs attention")

    def test_a_member_who_is_not_staff_never_sees_the_attention_section(self):
        for user in (self.neha, self.asha, self.sup, self.club):
            self.assertNotContains(self.home(user), "Needs attention", msg_prefix=user.email)
