"""
Views for the changes a club can make to a Coverage request after submitting it
(sub-events, event time), plus the roster-side changes shipped with them (two
verticals per member, the seeded first-year intake).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.constants import RequestStatus
from core.csv_import import import_rows, parse_csv
from core.models import Request, SubEvent, TaskType, TeamMember
from engine.tests.factories import COMMITTEE_EMAIL, FreeCalendar, build_world
from engine.workflow import process_new_request

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
ADMIN = "admin@iimsirmaur.ac.in"


def local(dt):
    return timezone.localtime(dt)


def fmt(dt):
    return local(dt).strftime("%Y-%m-%dT%H:%M")


def sub_rows(*rows):
    """Management data plus per-row fields for the new-request form's `sub` formset."""
    data = {
        "sub-TOTAL_FORMS": str(max(3, len(rows))),
        "sub-INITIAL_FORMS": "0",
        "sub-MIN_NUM_FORMS": "0",
        "sub-MAX_NUM_FORMS": "1000",
    }
    for i in range(max(3, len(rows))):
        row = rows[i] if i < len(rows) else {}
        for key in ("name", "start", "end", "venue", "notes"):
            data[f"sub-{i}-{key}"] = row.get(key, "")
    return data


class ScheduleBase(TestCase):
    def setUp(self):
        build_world()
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.poc = User.objects.create_user("poc", email=POC)
        self.stranger = User.objects.create_user("stranger", email="someone@iimsirmaur.ac.in")
        day = (timezone.now() + timedelta(days=5)).astimezone(timezone.get_current_timezone())
        self.start = day.replace(hour=10, minute=0, second=0, microsecond=0)
        self.end = self.start + timedelta(hours=2)

    def coverage(self, **overrides):
        values = dict(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL, venue="Hall",
            roles_needed=["Photographer"], platforms=[], status="New",
            event_start=self.start, event_end=self.end,
        )
        values.update(overrides)
        request_obj = Request.objects.create(**values)
        process_new_request(request_obj)
        request_obj.refresh_from_db()
        return request_obj


class SubEventAtCreationTests(ScheduleBase):
    """Only a multi-day event has a schedule of sub-events when it is raised."""

    def post_form(self, **extra):
        first = local(self.start).date()
        body = {
            "type": "Coverage", "event_name": "Annual Fest", "event_kind": "multi",
            "start_date": first.isoformat(), "end_date": (first + timedelta(days=2)).isoformat(),
            "roles_needed": ["Photographer"], "platforms": ["Instagram"], "requester": "Marketing",
        }
        body.update(extra)
        self.client.force_login(self.club)
        return self.client.post(reverse("request-new"), body)

    def test_sub_events_are_saved_with_the_request(self):
        response = self.post_form(
            **sub_rows(
                {"name": "Inauguration", "start": fmt(self.start), "end": fmt(self.start + timedelta(hours=1)), "venue": "Hall A"},
                {"name": "Panel", "start": fmt(self.start + timedelta(days=1)), "end": fmt(self.start + timedelta(days=1, hours=2)), "notes": "guests at 10:45"},
            )
        )
        request_obj = Request.objects.get(event_name="Annual Fest")
        self.assertRedirects(response, reverse("request-detail", args=[request_obj.pk]))
        self.assertTrue(request_obj.is_multiday)
        self.assertEqual(
            list(request_obj.sub_events.values_list("name", flat=True)), ["Inauguration", "Panel"]
        )

    def test_untouched_blank_rows_are_skipped(self):
        self.post_form(**sub_rows())
        self.assertEqual(SubEvent.objects.count(), 0)
        self.assertTrue(Request.objects.filter(event_name="Annual Fest").exists())

    def test_a_request_without_any_sub_event_data_still_works(self):
        self.post_form()
        self.assertTrue(Request.objects.filter(event_name="Annual Fest").exists())

    def test_sub_events_must_fall_inside_the_main_events_dates(self):
        early = self.start - timedelta(days=1)  # the day before the first day
        response = self.post_form(
            **sub_rows({"name": "Setup", "start": fmt(early), "end": fmt(early + timedelta(hours=1))})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must fall within the event")
        self.assertFalse(Request.objects.filter(event_name="Annual Fest").exists())

    def test_a_sub_event_must_end_after_it_starts(self):
        response = self.post_form(
            **sub_rows({"name": "Bad", "start": fmt(self.end), "end": fmt(self.start)})
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "must end after it starts")
        self.assertFalse(Request.objects.filter(event_name="Annual Fest").exists())

    def test_a_partly_filled_row_is_rejected(self):
        response = self.post_form(**sub_rows({"name": "No times"}))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Request.objects.filter(event_name="Annual Fest").exists())

    def test_stray_rows_on_a_post_request_are_ignored(self):
        self.client.force_login(self.club)
        self.client.post(
            reverse("request-new"),
            {
                "type": "Post", "event_name": "Launch", "platforms": ["Instagram"],
                "content_links": "http://example.invalid/x", "requester": "Marketing",
                **sub_rows({"name": "Ignored", "start": fmt(self.start), "end": fmt(self.end)}),
            },
        )
        self.assertEqual(SubEvent.objects.count(), 0)
        self.assertTrue(Request.objects.filter(event_name="Launch").exists())

    def test_the_approval_email_lists_the_schedule(self):
        # Starting today is short notice, so the POC is emailed for approval — with
        # the sub-events.
        today = timezone.localdate()
        midday = timezone.make_aware(datetime.combine(today, datetime.min.time().replace(hour=12)))
        mail.outbox.clear()
        self.post_form(
            start_date=today.isoformat(), end_date=(today + timedelta(days=1)).isoformat(),
            **sub_rows({"name": "Opening", "start": fmt(midday), "end": fmt(midday + timedelta(hours=1))}),
        )
        notice = next(m for m in mail.outbox if "[Approval needed]" in m.subject)
        self.assertIn("Sub-events:", notice.body)
        self.assertIn("Opening", notice.body)


class SubEventAfterSubmissionTests(ScheduleBase):
    def setUp(self):
        super().setUp()
        self.request = self.coverage()
        self.add_url = reverse("request-subevent-add", args=[self.request.pk])
        self.body = {
            "new-name": "Q&A", "new-start": fmt(self.start), "new-end": fmt(self.start + timedelta(hours=1)),
            "new-venue": "Room 5", "new-notes": "",
        }

    def test_the_club_can_add_and_the_coordinator_and_poc_are_emailed(self):
        self.client.force_login(self.club)
        mail.outbox.clear()
        response = self.client.post(self.add_url, self.body)
        self.assertRedirects(response, reverse("request-detail", args=[self.request.pk]))
        self.assertEqual(self.request.sub_events.get().name, "Q&A")
        notice = next(m for m in mail.outbox if "[Schedule updated]" in m.subject)
        self.assertIn(self.request.coordinator_email, notice.to)
        self.assertIn(POC, notice.to)
        self.assertIn("added", notice.body)

    def test_the_club_can_edit_and_delete(self):
        self.client.force_login(self.club)
        self.client.post(self.add_url, self.body)
        sub = self.request.sub_events.get()

        edit = {
            f"se{sub.pk}-name": "Q&A (moved)", f"se{sub.pk}-start": fmt(self.start),
            f"se{sub.pk}-end": fmt(self.start + timedelta(hours=1)), f"se{sub.pk}-venue": "Room 9",
            f"se{sub.pk}-notes": "",
        }
        self.client.post(reverse("request-subevent-edit", args=[self.request.pk, sub.pk]), edit)
        sub.refresh_from_db()
        self.assertEqual((sub.name, sub.venue), ("Q&A (moved)", "Room 9"))

        mail.outbox.clear()
        self.client.post(reverse("request-subevent-delete", args=[self.request.pk, sub.pk]))
        self.assertEqual(self.request.sub_events.count(), 0)
        gone = next(m for m in mail.outbox if "[Schedule updated]" in m.subject)
        self.assertIn("deleted", gone.body)
        self.assertIn("Q&A (moved)", gone.body)

    def test_the_poc_may_also_add(self):
        self.client.force_login(self.poc)
        self.client.post(self.add_url, self.body)
        self.assertEqual(self.request.sub_events.count(), 1)

    def test_a_stranger_may_not(self):
        self.client.force_login(self.stranger)
        self.assertEqual(self.client.post(self.add_url, self.body).status_code, 403)
        self.assertEqual(self.request.sub_events.count(), 0)

    def test_a_sub_event_starting_within_48_hours_cannot_be_added(self):
        soon = timezone.now() + timedelta(hours=30)
        body = {**self.body, "new-start": fmt(soon), "new-end": fmt(soon + timedelta(hours=1))}
        self.client.force_login(self.club)
        response = self.client.post(self.add_url, body, follow=True)
        self.assertContains(response, "48 hours")
        self.assertEqual(self.request.sub_events.count(), 0)

    def test_an_invalid_sub_event_is_refused_with_a_message(self):
        self.client.force_login(self.club)
        bad = {**self.body, "new-start": fmt(self.end), "new-end": fmt(self.start)}
        response = self.client.post(self.add_url, bad, follow=True)
        self.assertContains(response, "must end after it starts")
        self.assertEqual(self.request.sub_events.count(), 0)

    def test_a_post_request_has_no_sub_events(self):
        post = Request.objects.create(type="Post", event_name="P", contact_email=COMMITTEE_EMAIL, status="Request Accepted")
        self.client.force_login(self.club)
        response = self.client.post(reverse("request-subevent-add", args=[post.pk]), self.body)
        self.assertEqual(response.status_code, 403)

    def test_detail_page_offers_the_add_form_and_edits_only_sub_events_still_48_hours_away(self):
        far = SubEvent.objects.create(
            request=self.request, name="Far", start=timezone.now() + timedelta(days=5),
            end=timezone.now() + timedelta(days=5, hours=1),
        )
        near = SubEvent.objects.create(
            request=self.request, name="Near", start=timezone.now() + timedelta(hours=20),
            end=timezone.now() + timedelta(hours=21),
        )
        self.client.force_login(self.club)
        page = self.client.get(reverse("request-detail", args=[self.request.pk]))
        self.assertContains(page, "Add a sub-event")
        self.assertContains(page, f'name="se{far.pk}-name"')  # editable
        self.assertNotContains(page, f'name="se{near.pk}-name"')  # too close: shown read-only
        self.assertContains(page, "Near")

    def test_a_close_sub_event_cannot_be_edited_or_deleted(self):
        near = SubEvent.objects.create(
            request=self.request, name="Near", start=timezone.now() + timedelta(hours=20),
            end=timezone.now() + timedelta(hours=21),
        )
        self.client.force_login(self.club)
        edit = {
            f"se{near.pk}-name": "Changed", f"se{near.pk}-start": fmt(near.start),
            f"se{near.pk}-end": fmt(near.end), f"se{near.pk}-venue": "", f"se{near.pk}-notes": "",
        }
        self.client.post(reverse("request-subevent-edit", args=[self.request.pk, near.pk]), edit)
        self.client.post(reverse("request-subevent-delete", args=[self.request.pk, near.pk]))
        near.refresh_from_db()
        self.assertEqual(near.name, "Near")

    def test_a_far_sub_event_cannot_be_dragged_into_the_last_48_hours(self):
        far = SubEvent.objects.create(
            request=self.request, name="Far", start=timezone.now() + timedelta(days=5),
            end=timezone.now() + timedelta(days=5, hours=1),
        )
        soon = timezone.now() + timedelta(hours=20)
        self.client.force_login(self.club)
        self.client.post(
            reverse("request-subevent-edit", args=[self.request.pk, far.pk]),
            {
                f"se{far.pk}-name": "Far", f"se{far.pk}-start": fmt(soon),
                f"se{far.pk}-end": fmt(soon + timedelta(hours=1)), f"se{far.pk}-venue": "", f"se{far.pk}-notes": "",
            },
        )
        far.refresh_from_db()
        self.assertGreater(far.start, timezone.now() + timedelta(days=4))

    def test_the_main_events_24_hour_cutoff_no_longer_hides_the_sub_event_form(self):
        # Sub-events are governed by their own 48-hour cutoff, not the main event's.
        tomorrow = timezone.now() + timedelta(hours=10)
        Request.objects.filter(pk=self.request.pk).update(event_start=tomorrow, event_end=tomorrow + timedelta(hours=2))
        self.client.force_login(self.club)
        self.assertContains(self.client.get(reverse("request-detail", args=[self.request.pk])), "Add a sub-event")


class EventTimeChangeViewTests(ScheduleBase):
    def setUp(self):
        super().setUp()
        self.request = self.coverage()
        self.url = reverse("request-edit-time", args=[self.request.pk])

    def change(self, start="14:00", end="16:30", user=None):
        self.client.force_login(user or self.club)
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            return self.client.post(self.url, {"start_time": start, "end_time": end})

    def test_changes_the_time_and_never_the_date(self):
        old_date = local(self.start).date()
        response = self.change()
        self.assertRedirects(response, reverse("request-detail", args=[self.request.pk]))
        self.request.refresh_from_db()
        self.assertEqual(local(self.request.event_start).strftime("%H:%M"), "14:00")
        self.assertEqual(local(self.request.event_end).strftime("%H:%M"), "16:30")
        self.assertEqual(local(self.request.event_start).date(), old_date)
        self.assertEqual(local(self.request.event_end).date(), old_date)

    def test_a_multi_day_event_keeps_each_of_its_own_dates(self):
        end_day = self.end + timedelta(days=2)
        Request.objects.filter(pk=self.request.pk).update(event_end=end_day)
        first_date, last_date = local(self.start).date(), local(end_day).date()
        self.change(start="09:00", end="17:00")
        self.request.refresh_from_db()
        self.assertEqual(local(self.request.event_start).date(), first_date)
        self.assertEqual(local(self.request.event_end).date(), last_date)

    def test_the_form_has_no_date_field_to_tamper_with(self):
        self.client.force_login(self.club)
        page = self.client.get(reverse("request-detail", args=[self.request.pk]))
        self.assertContains(page, 'name="start_time"')
        self.assertNotContains(page, 'name="event_start"')
        # Smuggling in a date-shaped value changes nothing: it isn't a field.
        old = self.request.event_start
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            self.client.post(self.url, {"start_time": "10:00", "end_time": "12:00", "event_start": "2031-01-01T10:00"})
        self.request.refresh_from_db()
        self.assertEqual(self.request.event_start, old)

    def test_refused_within_24_hours_of_the_event(self):
        soon = timezone.now() + timedelta(hours=10)
        Request.objects.filter(pk=self.request.pk).update(event_start=soon, event_end=soon + timedelta(hours=2))
        self.assertEqual(self.change().status_code, 403)

    def test_the_start_cannot_be_moved_inside_the_24_hour_window(self):
        tz = timezone.get_current_timezone()
        now = timezone.make_aware(datetime(2031, 3, 10, 12, 0), tz)
        start = timezone.make_aware(datetime(2031, 3, 11, 16, 0), tz)  # 28h away: editable
        Request.objects.filter(pk=self.request.pk).update(event_start=start, event_end=start + timedelta(hours=2))

        with mock.patch("django.utils.timezone.now", return_value=now):
            self.change(start="10:00", end="18:00")  # 22h away: inside the window
        self.request.refresh_from_db()
        self.assertEqual(self.request.event_start, start)

        with mock.patch("django.utils.timezone.now", return_value=now):
            self.change(start="14:00", end="18:00")  # 26h away: still fine
        self.request.refresh_from_db()
        self.assertEqual(local(self.request.event_start).strftime("%H:%M"), "14:00")

    def test_refused_for_a_stranger_but_allowed_for_the_poc(self):
        self.assertEqual(self.change(user=self.stranger).status_code, 403)
        self.assertEqual(self.change(user=self.poc).status_code, 302)
        self.request.refresh_from_db()
        self.assertEqual(local(self.request.event_start).strftime("%H:%M"), "14:00")

    def test_end_must_be_after_start(self):
        self.change(start="15:00", end="14:00")
        self.request.refresh_from_db()
        self.assertEqual(self.request.event_start, self.start)

    def test_a_post_request_has_no_event_time(self):
        post = Request.objects.create(type="Post", event_name="P", contact_email=COMMITTEE_EMAIL, status="Request Accepted")
        self.client.force_login(self.club)
        self.assertEqual(
            self.client.post(reverse("request-edit-time", args=[post.pk]), {"start_time": "10:00", "end_time": "11:00"}).status_code,
            403,
        )

    def test_the_change_is_emailed_and_logged(self):
        mail.outbox.clear()
        self.change()
        notice = next(m for m in mail.outbox if "[Time changed]" in m.subject)
        self.assertIn(self.request.supervisor_email, notice.to)
        from core.models import ActivityLog

        self.assertTrue(ActivityLog.objects.filter(event="event-time-changed", ref_code=self.request.ref_code).exists())


class VerticalAdminTests(ScheduleBase):
    def setUp(self):
        super().setUp()
        self.member = TeamMember.objects.get(email="neha@iimsirmaur.ac.in")  # Graphic Designs
        self.url = reverse("set-member-verticals")

    def post(self, user, **data):
        self.client.force_login(user)
        return self.client.post(self.url, {"member_email": self.member.email, **data})

    def test_staff_can_set_both_verticals(self):
        self.post(self.poc, vertical="Videography", secondary_vertical="Photography")
        self.member.refresh_from_db()
        self.assertEqual((self.member.vertical, self.member.secondary_vertical), ("Videography", "Photography"))
        self.assertEqual(self.member.vertical_label, "Videography / Photography")

    def test_secondary_needs_a_primary_and_must_differ(self):
        self.post(self.poc, vertical="", secondary_vertical="Photography")
        self.post(self.poc, vertical="Photography", secondary_vertical="Photography")
        self.member.refresh_from_db()
        self.assertEqual((self.member.vertical, self.member.secondary_vertical), ("Graphic Designs", ""))

    def test_unknown_vertical_is_rejected(self):
        self.post(self.poc, vertical="Astrology")
        self.member.refresh_from_db()
        self.assertEqual(self.member.vertical, "Graphic Designs")

    def test_a_member_cannot_edit_verticals(self):
        user = User.objects.create_user("neha", email=self.member.email)
        self.assertEqual(self.post(user, vertical="Photography").status_code, 403)

    def test_the_admin_page_shows_a_row_per_member_with_both_dropdowns(self):
        self.client.force_login(self.poc)
        page = self.client.get(reverse("portal-admin"))
        self.assertContains(page, 'name="secondary_vertical"')
        self.assertContains(page, "core/seed_data.py")  # the deploy-resets-verticals warning

    def test_making_a_member_head_keeps_verticals_they_already_have(self):
        self.member.secondary_vertical = "Photography"
        self.member.save()
        self.client.force_login(self.poc)
        self.client.post(reverse("vertical-head"), {"member_email": self.member.email, "vertical": "Photography"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.domain_head_of, "Photography")
        self.assertEqual((self.member.vertical, self.member.secondary_vertical), ("Graphic Designs", "Photography"))

    def test_making_someone_head_of_a_vertical_they_are_not_in_sets_it_as_primary(self):
        self.client.force_login(self.poc)
        self.client.post(reverse("vertical-head"), {"member_email": self.member.email, "vertical": "Videography"})
        self.member.refresh_from_db()
        self.assertEqual(self.member.vertical, "Videography")


class CsvVerticalTests(TestCase):
    def rows(self, text):
        return import_rows(parse_csv(text))

    def test_secondary_vertical_and_aliases(self):
        self.rows("name,email,vertical,secondaryVertical,year\nRia,ria@i.ac.in,Graphic Design,content writing,1")
        ria = TeamMember.objects.get(email="ria@i.ac.in")
        self.assertEqual((ria.vertical, ria.secondary_vertical), ("Graphic Designs", "Content Writing"))

    def test_a_file_without_the_column_leaves_the_secondary_alone(self):
        TeamMember.objects.create(email="ria@i.ac.in", name="Ria", vertical="Photography", secondary_vertical="Videography", year=1)
        self.rows("name,email,vertical,year\nRia,ria@i.ac.in,Photography,1")
        self.assertEqual(TeamMember.objects.get(email="ria@i.ac.in").secondary_vertical, "Videography")

    def test_a_file_with_a_blank_column_clears_it(self):
        TeamMember.objects.create(email="ria@i.ac.in", name="Ria", vertical="Photography", secondary_vertical="Videography", year=1)
        self.rows("name,email,vertical,secondaryVertical,year\nRia,ria@i.ac.in,Photography,,1")
        self.assertEqual(TeamMember.objects.get(email="ria@i.ac.in").secondary_vertical, "")

    def test_new_members_start_with_no_strikes(self):
        self.rows("name,email,vertical,year\nRia,ria@i.ac.in,Photography,1")
        ria = TeamMember.objects.get(email="ria@i.ac.in")
        self.assertEqual((ria.yellow_strikes, ria.red_strikes, ria.points), (0, 0, 0))


class SeedTests(TestCase):
    def seed(self):
        call_command("seed_real_data", stdout=StringIO())

    def test_the_fourteen_first_years_are_seeded_correctly(self):
        self.seed()
        intake = TeamMember.objects.filter(year=1, campus="MBA Campus")
        self.assertEqual(intake.count(), 14)
        self.assertFalse(TeamMember.objects.filter(email__startswith="mba26068@").exists())  # Yogesh: not added

        ayush = TeamMember.objects.get(email="mba26142@iimsirmaur.ac.in")
        self.assertEqual(ayush.name, "Ayush")
        self.assertEqual((ayush.vertical, ayush.secondary_vertical), ("Photography", "Videography"))
        self.assertEqual(ayush.phone, "8287430993")

        om = TeamMember.objects.get(email="mba26106@iimsirmaur.ac.in")
        self.assertEqual((om.vertical, om.secondary_vertical), ("Graphic Designs", "Photography"))
        self.assertEqual(om.skills, ["Graphic design", "Photography", "Photo Editing"])

        pragyi = TeamMember.objects.get(email="mba26254@iimsirmaur.ac.in")
        self.assertEqual(pragyi.skills, ["Photography", "Photo Editing", "Content Writing"])

        ishan = TeamMember.objects.get(email="mba26154@iimsirmaur.ac.in")
        self.assertEqual(ishan.skills, ["Videography", "Video Editing", "Photography", "Photo Editing"])

    def test_seeding_twice_changes_nothing_and_keeps_portal_edits(self):
        self.seed()
        count = TeamMember.objects.count()
        member = TeamMember.objects.get(email="mba26142@iimsirmaur.ac.in")
        member.phone = "0000000000"
        member.points = 40
        member.yellow_strikes = 2
        member.save()
        self.seed()
        self.assertEqual(TeamMember.objects.count(), count)
        member.refresh_from_db()
        self.assertEqual((member.phone, member.points, member.yellow_strikes), ("0000000000", 40, 2))

    def test_second_years_hold_no_coordination_or_vetting_skill_and_are_all_second_year(self):
        self.seed()
        for m in TeamMember.objects.filter(year=2):
            self.assertNotIn("Vetting", m.skills)
            self.assertNotIn("Coordination", m.skills)

    def test_task_types_match_the_new_rules(self):
        self.seed()
        self.assertEqual(TaskType.objects.get(task="Event Coordinator").required_skill, "")
        supervisor = TaskType.objects.get(task="Task Supervisor")
        self.assertEqual((supervisor.points, supervisor.required_skill), (0, ""))
        self.assertFalse(TaskType.objects.get(task="Vetter").internal_assignable)

    def test_a_seeded_world_can_actually_staff_an_mba_coverage_request(self):
        self.seed()
        club_email = "sapient@iimsirmaur.ac.in"
        soon = timezone.now() + timedelta(days=5)
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=club_email, campus="MBA Campus",
            roles_needed=["Photographer"], platforms=[], status=RequestStatus.NEW,
            event_start=soon, event_end=soon + timedelta(hours=2),
        )
        process_new_request(request_obj)
        request_obj.refresh_from_db()
        tasks = {t.task: t for t in request_obj.tasks.all()}
        for name in ("Photographer", "Photo Editor", "Event Coordinator", "Task Supervisor"):
            self.assertTrue(tasks[name].email, f"{name} was left unfilled: {tasks[name].reason}")
        first_years = set(TeamMember.objects.filter(year=1).values_list("email", flat=True))
        for name in ("Photographer", "Photo Editor", "Event Coordinator"):
            self.assertIn(tasks[name].email, first_years)
        second_years = set(TeamMember.objects.filter(year=2).values_list("email", flat=True))
        self.assertIn(tasks["Task Supervisor"].email, second_years)


class DashboardVerticalTests(ScheduleBase):
    def test_vertical_filter_matches_secondary_but_totals_use_primary(self):
        from ui.dashboard import compute_stats

        neha = TeamMember.objects.get(email="neha@iimsirmaur.ac.in")  # primary Graphic Designs
        neha.secondary_vertical = "Videography"
        neha.points = 7
        neha.save()
        stats = compute_stats({"vertical": "Videography"})
        self.assertEqual([r["email"] for r in stats["leaderboard"]], ["neha@iimsirmaur.ac.in"])
        self.assertEqual(stats["leaderboard"][0]["vertical"], "Graphic Designs / Videography")
        # The by-vertical table counts her once, under her primary vertical only.
        self.assertEqual([r["vertical"] for r in stats["by_vertical"]], ["Graphic Designs"])
        self.assertEqual(stats["totals"]["total_points"], 7)
