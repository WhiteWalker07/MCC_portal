"""
Single-day vs multi-day Coverage events: the toggle on the request form, what a
multi-day event stores (dates only, whole days, no venue), the sub-event rules
that go with it, and the calendar behaviour.

Django's test client + locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.models import Request, SubEvent, Task, TeamMember
from engine.tests.factories import COMMITTEE_EMAIL, NEHA, build_world
from engine.workflow import process_new_request
from services import calendar as calendar_module

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"


def local(dt):
    return timezone.localtime(dt)


def fmt(dt):
    return local(dt).strftime("%Y-%m-%dT%H:%M")


def sub_rows(*rows):
    data = {"sub-TOTAL_FORMS": str(max(1, len(rows))), "sub-INITIAL_FORMS": "0",
            "sub-MIN_NUM_FORMS": "0", "sub-MAX_NUM_FORMS": "1000"}
    for i in range(max(1, len(rows))):
        row = rows[i] if i < len(rows) else {}
        for key in ("name", "start", "end", "venue", "notes"):
            data[f"sub-{i}-{key}"] = row.get(key, "")
    return data


class Base(TestCase):
    def setUp(self):
        build_world()
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.poc = User.objects.create_user("poc", email=POC)
        day = (timezone.now() + timedelta(days=6)).astimezone(timezone.get_current_timezone())
        self.first = day.date()
        self.last = self.first + timedelta(days=2)
        self.at10 = day.replace(hour=10, minute=0, second=0, microsecond=0)
        mail.outbox.clear()

    def multi_body(self, **extra):
        body = {
            "type": "Coverage", "event_name": "Fest Week", "event_kind": "multi",
            "start_date": self.first.isoformat(), "end_date": self.last.isoformat(),
            "roles_needed": ["Photographer"], "platforms": ["Instagram"], "requester": "Marketing",
            **sub_rows(),
        }
        body.update(extra)
        return body

    def submit(self, body):
        self.client.force_login(self.club)
        return self.client.post(reverse("request-new"), body)

    def multiday_request(self):
        self.submit(self.multi_body())
        return Request.objects.get(event_name="Fest Week")


class CreationFormTests(Base):
    def test_the_form_offers_the_toggle_the_date_fields_and_the_plus_button(self):
        self.client.force_login(self.club)
        page = self.client.get(reverse("request-new"))
        self.assertContains(page, "Single-day event")
        self.assertContains(page, "Multi-day event")
        self.assertContains(page, 'name="start_date"')
        self.assertContains(page, 'id="add-sub"')
        self.assertContains(page, 'id="sub-template"')
        self.assertContains(page, "__prefix__")  # the row the + button clones
        self.assertContains(page, "js-remove-sub")  # each row has a Delete button

    def test_single_day_is_the_default(self):
        self.client.force_login(self.club)
        page = self.client.get(reverse("request-new"))
        self.assertContains(page, '<div id="multi-fields" class="stack" hidden>')
        self.assertNotContains(page, '<div id="single-fields" class="stack" hidden>')

    def test_a_multi_day_request_keeps_only_dates_and_treats_them_as_whole_days(self):
        request_obj = self.multiday_request()
        self.assertTrue(request_obj.is_multiday)
        self.assertEqual(local(request_obj.event_start).strftime("%Y-%m-%d %H:%M"), f"{self.first} 00:00")
        self.assertEqual(local(request_obj.event_end).strftime("%Y-%m-%d %H:%M"), f"{self.last} 23:59")
        self.assertEqual(request_obj.venue, "")

    def test_a_venue_sent_with_a_multi_day_event_is_dropped(self):
        self.submit(self.multi_body(venue="Main Hall"))
        self.assertEqual(Request.objects.get().venue, "")

    def test_the_single_day_fields_are_not_needed_for_a_multi_day_event(self):
        # No event_start / event_end / venue at all, and it still goes through.
        self.submit(self.multi_body())
        self.assertEqual(Request.objects.count(), 1)

    def test_a_single_day_event_is_unchanged(self):
        self.client.force_login(self.club)
        self.client.post(
            reverse("request-new"),
            {
                "type": "Coverage", "event_name": "One Day", "event_kind": "single",
                "event_start": fmt(self.at10), "event_end": fmt(self.at10 + timedelta(hours=2)),
                "venue": "Auditorium", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
                "requester": "Marketing",
            },
        )
        request_obj = Request.objects.get(event_name="One Day")
        self.assertFalse(request_obj.is_multiday)
        self.assertEqual(request_obj.venue, "Auditorium")

    def test_a_request_with_no_kind_at_all_is_treated_as_single_day(self):
        self.client.force_login(self.club)
        self.client.post(
            reverse("request-new"),
            {
                "type": "Coverage", "event_name": "Old Client",
                "event_start": fmt(self.at10), "event_end": fmt(self.at10 + timedelta(hours=2)),
                "venue": "Hall", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
            },
        )
        self.assertFalse(Request.objects.get(event_name="Old Client").is_multiday)

    def test_a_single_day_event_ignores_sub_event_rows_sent_with_it(self):
        self.client.force_login(self.club)
        self.client.post(
            reverse("request-new"),
            {
                "type": "Coverage", "event_name": "One Day", "event_kind": "single",
                "event_start": fmt(self.at10), "event_end": fmt(self.at10 + timedelta(hours=2)),
                "venue": "Hall", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
                **sub_rows({"name": "Ignored", "start": fmt(self.at10), "end": fmt(self.at10 + timedelta(hours=1))}),
            },
        )
        self.assertTrue(Request.objects.filter(event_name="One Day").exists())
        self.assertEqual(SubEvent.objects.count(), 0)

    def test_both_dates_are_required_and_must_be_in_order(self):
        for extra, message in (
            ({"start_date": ""}, "first day"),
            ({"end_date": ""}, "last day"),
            ({"end_date": (self.first - timedelta(days=1)).isoformat()}, "end before it starts"),
            ({"start_date": (timezone.localdate() - timedelta(days=2)).isoformat(),
              "end_date": timezone.localdate().isoformat()}, "in the past"),
        ):
            response = self.submit(self.multi_body(**extra))
            self.assertEqual(response.status_code, 200, extra)
            self.assertContains(response, message)
        self.assertEqual(Request.objects.count(), 0)

    def test_a_one_day_range_is_allowed(self):
        self.submit(self.multi_body(end_date=self.first.isoformat()))
        self.assertEqual(Request.objects.count(), 1)

    def test_several_sub_events_from_the_plus_button_are_saved(self):
        rows = [
            {"name": f"Part {i}", "start": fmt(self.at10 + timedelta(hours=i)),
             "end": fmt(self.at10 + timedelta(hours=i, minutes=45)), "venue": f"Room {i}"}
            for i in range(5)
        ]
        self.submit(self.multi_body(**sub_rows(*rows)))
        self.assertEqual(SubEvent.objects.count(), 5)

    def test_a_deleted_row_is_just_an_empty_row_and_is_skipped(self):
        # The page's Delete button clears a row and hides it, so what the server sees
        # is a blank row between filled ones: it must not block or be saved.
        rows = [
            {"name": "Keep 1", "start": fmt(self.at10), "end": fmt(self.at10 + timedelta(hours=1))},
            {},  # the deleted one
            {"name": "Keep 2", "start": fmt(self.at10 + timedelta(hours=3)), "end": fmt(self.at10 + timedelta(hours=4))},
        ]
        self.submit(self.multi_body(**sub_rows(*rows)))
        self.assertEqual(list(SubEvent.objects.order_by("start").values_list("name", flat=True)), ["Keep 1", "Keep 2"])

    def test_a_sub_event_after_the_last_day_is_refused(self):
        late = self.at10 + timedelta(days=5)
        response = self.submit(
            self.multi_body(**sub_rows({"name": "Late", "start": fmt(late), "end": fmt(late + timedelta(hours=1))}))
        )
        self.assertContains(response, "must fall within the event")
        self.assertEqual(Request.objects.count(), 0)


class RequesterLockTests(Base):
    """A committee raises requests as itself: its name can't be edited."""

    def test_the_committee_name_is_shown_and_read_only(self):
        self.client.force_login(self.club)
        page = self.client.get(reverse("request-new"))
        html = page.content.decode()
        start = html.index('name="requester"')
        tag = html[html.rindex("<input", 0, start): html.index("/>", start)]
        self.assertIn('value="Marketing"', tag)
        self.assertIn("readonly", tag)

    def test_a_changed_name_is_ignored_and_the_committee_name_is_stored(self):
        self.submit(self.multi_body(requester="Somebody Else"))
        self.assertEqual(Request.objects.get().requester, "Marketing")

    def test_a_blank_name_still_ends_up_as_the_committee_name(self):
        self.submit(self.multi_body(requester=""))
        self.assertEqual(Request.objects.get().requester, "Marketing")

    def test_it_is_locked_for_a_post_raised_by_a_committee_too(self):
        self.client.force_login(self.club)
        self.client.post(
            reverse("request-new"),
            {"type": "Post", "event_name": "Launch", "platforms": ["Instagram"],
             "content_links": "http://example.invalid/x", "requester": "Somebody Else"},
        )
        self.assertEqual(Request.objects.get(event_name="Launch").requester, "Marketing")

    def test_an_ordinary_account_can_still_type_its_own_name(self):
        someone = User.objects.create_user("someone", email="someone@iimsirmaur.ac.in")
        self.client.force_login(someone)
        page = self.client.get(reverse("request-new"))
        html = page.content.decode()
        start = html.index('name="requester"')
        self.assertNotIn("readonly", html[html.rindex("<input", 0, start): html.index("/>", start)])
        self.client.post(
            reverse("request-new"),
            {"type": "Post", "event_name": "Hello", "platforms": ["Instagram"],
             "content_links": "http://example.invalid/x", "requester": "Priya"},
        )
        self.assertEqual(Request.objects.get(event_name="Hello").requester, "Priya")


class MultiDayBehaviourTests(Base):
    def test_the_detail_page_shows_dates_not_a_time_or_a_venue_form(self):
        request_obj = self.multiday_request()
        page = self.client.get(reverse("request-detail", args=[request_obj.pk]))
        self.assertContains(page, "multi-day")
        self.assertNotContains(page, 'name="start_time"')
        self.assertNotContains(page, 'action="' + reverse("request-edit-venue", args=[request_obj.pk]))
        self.assertContains(page, "Each sub-event has its own")

    def test_the_time_and_venue_cannot_be_changed(self):
        request_obj = self.multiday_request()
        self.assertEqual(
            self.client.post(reverse("request-edit-time", args=[request_obj.pk]), {"start_time": "09:00", "end_time": "17:00"}).status_code,
            403,
        )
        self.assertEqual(
            self.client.post(reverse("request-edit-venue", args=[request_obj.pk]), {"venue": "Somewhere"}).status_code,
            403,
        )

    def test_sub_events_can_be_added_later_inside_the_dates_with_the_48_hour_rule(self):
        request_obj = self.multiday_request()
        url = reverse("request-subevent-add", args=[request_obj.pk])
        ok = self.at10 + timedelta(days=1)
        self.client.post(url, {"new-name": "Day 2 talk", "new-start": fmt(ok), "new-end": fmt(ok + timedelta(hours=1)),
                               "new-venue": "Hall B", "new-notes": ""})
        self.assertEqual(request_obj.sub_events.get().name, "Day 2 talk")

        outside = self.at10 + timedelta(days=9)
        response = self.client.post(
            url, {"new-name": "Outside", "new-start": fmt(outside), "new-end": fmt(outside + timedelta(hours=1)),
                  "new-venue": "", "new-notes": ""}, follow=True,
        )
        self.assertContains(response, "must fall within the event")
        self.assertEqual(request_obj.sub_events.count(), 1)

    def test_editing_a_sub_event_cannot_move_it_outside_the_dates(self):
        request_obj = self.multiday_request()
        sub = SubEvent.objects.create(
            request=request_obj, name="Talk", start=self.at10, end=self.at10 + timedelta(hours=1)
        )
        outside = self.at10 + timedelta(days=9)
        self.client.force_login(self.club)
        self.client.post(
            reverse("request-subevent-edit", args=[request_obj.pk, sub.pk]),
            {f"se{sub.pk}-name": "Talk", f"se{sub.pk}-start": fmt(outside),
             f"se{sub.pk}-end": fmt(outside + timedelta(hours=1)), f"se{sub.pk}-venue": "", f"se{sub.pk}-notes": ""},
        )
        sub.refresh_from_db()
        self.assertEqual(local(sub.start).date(), self.first)

    def test_task_deadlines_count_from_the_end_of_the_last_day(self):
        request_obj = self.multiday_request()
        photographer = request_obj.tasks.get(task="Photographer")
        self.assertEqual(photographer.deadline, request_obj.event_end)
        editor = request_obj.tasks.get(task="Photo Editor")
        self.assertEqual(editor.deadline, request_obj.event_end + timedelta(hours=24))

    def test_lists_show_the_date_range(self):
        self.multiday_request()
        page = self.client.get(reverse("request-list"))
        self.assertContains(page, f"{self.first:%d %b} – {self.last:%d %b %Y}")

    def test_the_poc_sees_dates_and_the_schedule_when_approving(self):
        today = timezone.localdate()
        self.submit(self.multi_body(start_date=today.isoformat(), end_date=(today + timedelta(days=2)).isoformat()))
        request_obj = Request.objects.get(event_name="Fest Week")
        self.assertEqual(request_obj.status, "Pending for POC approval")  # starts today: short notice
        self.client.force_login(self.poc)
        page = self.client.get(reverse("approval-detail", args=[request_obj.pk]))
        self.assertContains(page, "multi-day")
        self.assertContains(page, "Each sub-event has its own")


class CalendarTests(Base):
    """A multi-day event has no single window, so no holds and no busy-checks."""

    class Recorder:
        def __init__(self):
            self.holds, self.reminders, self.free_checks = [], [], []

        def is_free(self, email, start, end):
            self.free_checks.append(email)
            return False  # everyone is "busy": would exclude everybody if it were consulted

        def create_hold(self, **kwargs):
            self.holds.append(kwargs)

        def create_reminder(self, **kwargs):
            self.reminders.append(kwargs)

    def run_with(self, recorder, **request_kwargs):
        from unittest import mock

        start = timezone.now() + timedelta(days=7)
        request_obj = Request.objects.create(
            type="Coverage", event_name="Fest", contact_email=COMMITTEE_EMAIL, roles_needed=["Photographer"],
            platforms=[], status="New", event_start=start, event_end=start + timedelta(days=2),
            **request_kwargs,
        )
        with mock.patch("engine.workflow.calendar_service", return_value=recorder), \
                mock.patch("engine.notify.calendar_service", return_value=recorder):
            process_new_request(request_obj)
        request_obj.refresh_from_db()
        return request_obj

    def test_multi_day_events_are_staffed_even_when_every_calendar_is_busy_and_get_no_holds(self):
        recorder = self.Recorder()
        request_obj = self.run_with(recorder, is_multiday=True)
        self.assertTrue(request_obj.tasks.get(task="Photographer").email)  # staffed
        self.assertEqual(recorder.free_checks, [])
        self.assertEqual(recorder.holds, [])
        self.assertTrue(recorder.reminders)  # they still get the deadline reminder

    def test_a_single_day_event_still_checks_calendars_and_books_holds(self):
        recorder = self.Recorder()
        request_obj = self.run_with(recorder, is_multiday=False)
        self.assertTrue(recorder.free_checks)
        self.assertEqual(request_obj.tasks.get(task="Photographer").status, "UNFILLED")  # all "busy"
