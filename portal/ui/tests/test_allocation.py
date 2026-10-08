"""
The "choose the team" step for a request a POC/Admin enters on a club's behalf:
nothing is saved or sent until they confirm, they can change anyone, their picks are
checked against the real rules, and anything they leave alone is picked as usual.

Django's test client + locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from core.constants import Availability
from core.models import ActivityLog, Committee, Request, Task, TeamMember
from engine.tests.factories import ASHA, COMMITTEE_EMAIL, NEHA, SUPERVISOR, build_world

User = get_user_model()

POC = "poc@iimsirmaur.ac.in"
RAVI = "ravi@iimsirmaur.ac.in"
VIC = "vic@iimsirmaur.ac.in"
SUE = "sue@iimsirmaur.ac.in"
OLI = "oli@iimsirmaur.ac.in"


def fmt(dt):
    return timezone.localtime(dt).strftime("%Y-%m-%dT%H:%M")


class Base(TestCase):
    def setUp(self):
        build_world()
        mk = lambda e, n, year=1, skills=(), **x: TeamMember.objects.create(
            email=e, name=n, year=year, campus="Permanent", vertical="Photography", skills=list(skills), **x
        )
        self.ravi = mk(RAVI, "Ravi", skills=["Photography"])
        self.vic = mk(VIC, "Vic")
        self.sue = mk(SUE, "Sue", year=2)
        self.oli = mk(OLI, "Oli", availability=Availability.OUT)
        self.committee = Committee.objects.get(email=COMMITTEE_EMAIL)
        self.poc = User.objects.create_user("poc", email=POC)
        self.club = User.objects.create_user("club", email=COMMITTEE_EMAIL)
        self.url = f"{reverse('request-new')}?for={self.committee.pk}"
        mail.outbox.clear()

    def form(self, **extra):
        start = timezone.now() + timedelta(days=5)
        body = {
            "type": "Coverage", "event_name": "Fest", "event_kind": "single",
            "event_start": fmt(start), "event_end": fmt(start + timedelta(hours=2)),
            "venue": "Hall", "roles_needed": ["Photographer"], "platforms": ["Instagram"],
        }
        body.update(extra)
        return body

    def step_one(self, **extra):
        self.client.force_login(self.poc)
        return self.client.post(self.url, self.form(**extra))

    def confirm(self, picks=None, **extra):
        body = {**self.form(**extra), "step": "confirm"}
        body.update({f"pick:{task}": email for task, email in (picks or {}).items()})
        self.client.force_login(self.poc)
        return self.client.post(self.url, body)

    def task(self, request_obj, name):
        return request_obj.tasks.get(task=name)


class PreviewStepTests(Base):
    def test_the_first_step_shows_the_team_page_and_saves_nothing_and_sends_nothing(self):
        before = Committee.objects.get(pk=self.committee.pk).last_seq
        response = self.step_one()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose the team")
        self.assertContains(response, "Nothing is saved or sent yet")
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(Task.objects.count(), 0)
        self.assertEqual(mail.outbox, [])
        self.assertEqual(Committee.objects.get(pk=self.committee.pk).last_seq, before)  # no ID burned

    def test_it_makes_no_calendar_bookings_while_previewing(self):
        with mock.patch("engine.notify.calendar_service") as booked:
            self.step_one()
        booked.assert_not_called()

    def test_a_row_per_task_each_preselected_with_the_systems_suggestion(self):
        response = self.step_one()
        rows = {r.task: r for r in response.context["rows"]}
        self.assertEqual(set(rows), {"Photographer", "Photo Editor", "Event Coordinator", "Task Supervisor"})
        self.assertTrue(rows["Photographer"].selected)
        self.assertTrue(rows["Event Coordinator"].selected)
        self.assertIn(rows["Task Supervisor"].selected, {SUPERVISOR, SUE})  # either second-year may be suggested
        for row in rows.values():
            self.assertIn(row.selected, {e for e, _ in row.options} | {""})

    def test_the_editor_defaults_to_the_same_person_as_the_shoot(self):
        response = self.step_one()
        editor = next(r for r in response.context["rows"] if r.task == "Photo Editor")
        self.assertEqual((editor.selected, editor.follows), ("", "Photographer"))
        self.assertContains(response, "Same person as the Photographer")

    def test_the_dropdowns_offer_the_right_people_only(self):
        rows = {r.task: r for r in self.step_one().context["rows"]}
        hands_on = {e for e, _ in rows["Photographer"].options}
        self.assertTrue({ASHA, RAVI, VIC, NEHA} <= hands_on)
        self.assertNotIn(SUE, hands_on)  # a second-year only supervises
        self.assertNotIn(SUPERVISOR, hands_on)
        self.assertNotIn(OLI, hands_on)  # out of work
        supervisors = {e for e, _ in rows["Task Supervisor"].options}
        self.assertEqual(supervisors, {SUPERVISOR, SUE})

    def test_each_option_shows_name_verticals_and_points(self):
        rows = {r.task: r for r in self.step_one().context["rows"]}
        label = dict(rows["Photographer"].options)[RAVI]
        self.assertIn("Ravi", label)
        self.assertIn("Photography", label)
        self.assertIn("pts", label)

    def test_the_form_is_carried_so_nothing_has_to_be_typed_again(self):
        response = self.step_one(event_name="Carry Me")
        carried = dict(response.context["carried"])
        self.assertEqual(carried["event_name"], "Carry Me")
        self.assertEqual(carried["venue"], "Hall")
        self.assertNotIn("csrfmiddlewaretoken", carried)
        self.assertContains(response, '<input type="hidden" name="event_name" value="Carry Me" />')

    def test_back_returns_to_the_form_still_filled_in_and_saves_nothing(self):
        self.client.force_login(self.poc)
        response = self.client.post(self.url, {**self.form(event_name="Keep Me"), "step": "edit"})
        self.assertContains(response, 'value="Keep Me"')
        self.assertContains(response, "Next: choose the team")
        self.assertNotContains(response, "Choose the team")
        self.assertEqual(Request.objects.count(), 0)

    def test_a_form_with_errors_stays_on_the_form(self):
        response = self.step_one(event_start="")
        self.assertNotContains(response, "Choose the team")
        self.assertContains(response, "When does the event start")

    def test_the_form_button_says_next_for_staff_and_submit_otherwise(self):
        self.client.force_login(self.poc)
        self.assertContains(self.client.get(self.url), "Next: choose the team")
        self.client.force_login(self.club)
        self.assertContains(self.client.get(reverse("request-new")), "Submit request")

    def test_the_page_says_who_will_be_emailed(self):
        response = self.step_one()
        self.assertContains(response, COMMITTEE_EMAIL)
        self.assertContains(response, "Save and send emails")


class ConfirmedPicksTests(Base):
    def test_a_chosen_person_gets_the_task(self):
        self.confirm({"Photographer": RAVI, "Event Coordinator": VIC})
        request_obj = Request.objects.get()
        self.assertEqual(self.task(request_obj, "Photographer").email, RAVI)
        self.assertEqual(self.task(request_obj, "Event Coordinator").email, VIC)
        self.assertEqual(request_obj.coordinator_email, VIC)

    def test_the_editor_follows_a_chosen_shooter_unless_set_separately(self):
        self.confirm({"Photographer": RAVI})
        request_obj = Request.objects.get()
        self.assertEqual(self.task(request_obj, "Photo Editor").email, RAVI)
        self.assertEqual(self.task(request_obj, "Photo Editor").paired_task_id, self.task(request_obj, "Photographer").pk)

    def test_an_editor_can_be_given_to_someone_else(self):
        self.confirm({"Photographer": RAVI, "Photo Editor": ASHA})
        request_obj = Request.objects.get()
        self.assertEqual(self.task(request_obj, "Photographer").email, RAVI)
        self.assertEqual(self.task(request_obj, "Photo Editor").email, ASHA)

    def test_a_second_year_can_be_chosen_as_the_supervisor(self):
        self.confirm({"Task Supervisor": SUE})
        request_obj = Request.objects.get()
        self.assertEqual(request_obj.supervisor_email, SUE)
        self.assertEqual(self.task(request_obj, "Task Supervisor").email, SUE)

    @override_settings(PORTAL_RANDOM_TIE_BREAK=True)
    def test_a_chosen_person_steers_the_automatic_picks_away_from_them(self):
        # Vic is chosen as coordinator, so the system must not also give Vic the shoot,
        # however the random tie-break falls.
        for _ in range(20):
            Request.objects.all().delete()
            self.confirm({"Event Coordinator": VIC})
            self.assertNotEqual(self.task(Request.objects.get(), "Photographer").email, VIC)

    def test_what_is_left_on_the_system_is_still_picked_by_the_system(self):
        self.confirm({"Event Coordinator": VIC})
        request_obj = Request.objects.get()
        for name in ("Photographer", "Photo Editor", "Task Supervisor"):
            self.assertTrue(self.task(request_obj, name).email, name)

    def test_the_club_and_the_chosen_people_are_emailed_only_now(self):
        self.confirm({"Photographer": RAVI})
        accepted = next(m for m in mail.outbox if m.subject.startswith("[Accepted]"))
        self.assertEqual(accepted.to, [COMMITTEE_EMAIL])
        self.assertIn(RAVI, accepted.cc)
        assigned = {addr for m in mail.outbox if m.subject.startswith("[Assigned]") for addr in m.to}
        self.assertIn(RAVI, assigned)

    def test_it_is_accepted_straight_away_and_belongs_to_the_club(self):
        self.confirm({"Photographer": RAVI})
        request_obj = Request.objects.get()
        self.assertEqual(request_obj.status, "Request Accepted")
        self.assertEqual((request_obj.contact_email, request_obj.created_on_behalf_by), (COMMITTEE_EMAIL, POC))
        self.assertTrue(request_obj.ref_code.startswith("MKTG_"))

    def test_the_audit_log_says_which_assignees_were_chosen_by_hand(self):
        self.confirm({"Photographer": RAVI})
        logged = {e.detail for e in ActivityLog.objects.filter(event="proposed")}
        self.assertTrue(any("Photographer -> Ravi (chosen by" in d and POC in d for d in logged), logged)
        self.assertFalse(any("Task Supervisor" in d and "chosen by" in d for d in logged))

    def test_a_post_can_be_staffed_by_hand_too(self):
        self.client.force_login(self.poc)
        self.client.post(
            self.url,
            {"type": "Post", "event_name": "Launch", "platforms": ["Instagram"],
             "content_links": "http://example.invalid/x", "step": "confirm",
             "pick:Content Writer": VIC, "pick:Graphic Designer": NEHA},
        )
        request_obj = Request.objects.get()
        self.assertEqual(self.task(request_obj, "Content Writer").email, VIC)
        self.assertEqual(self.task(request_obj, "Graphic Designer").email, NEHA)

    def test_a_pick_for_a_role_the_request_does_not_have_is_ignored(self):
        self.confirm({"Videographer": VIC})
        self.assertEqual(Request.objects.count(), 1)
        self.assertFalse(Request.objects.get().tasks.filter(task="Videographer").exists())

    def test_confirming_with_no_picks_at_all_is_the_old_automatic_behaviour(self):
        self.confirm()
        request_obj = Request.objects.get()
        self.assertEqual(request_obj.status, "Request Accepted")
        self.assertEqual(request_obj.tasks.count(), 4)


class PicksAreCheckedTests(Base):
    def refused(self, picks):
        response = self.confirm(picks)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose the team")
        self.assertEqual(Request.objects.count(), 0)
        self.assertEqual(mail.outbox, [])
        return response

    def test_someone_out_of_work_is_refused_with_the_reason(self):
        response = self.refused({"Photographer": OLI})
        self.assertContains(response, "not eligible")
        self.assertContains(response, "Oli")

    def test_a_second_year_cannot_be_given_hands_on_work(self):
        response = self.refused({"Photographer": SUE})
        self.assertContains(response, "second-year")

    def test_a_first_year_cannot_be_the_supervisor(self):
        self.refused({"Task Supervisor": RAVI})

    def test_someone_not_on_the_team_is_refused(self):
        response = self.refused({"Photographer": "nobody@iimsirmaur.ac.in"})
        self.assertContains(response, "not on the team")

    def test_someone_busy_in_the_event_window_is_refused(self):
        from engine.tests.factories import BusyCalendar

        with mock.patch("engine.assignment.calendar_service", return_value=BusyCalendar()):
            response = self.refused({"Photographer": RAVI})
        self.assertContains(response, "busy during the event window")

    def test_someone_on_another_campus_is_refused(self):
        TeamMember.objects.filter(email=RAVI).update(campus="Elsewhere")
        self.refused({"Photographer": RAVI})

    def test_a_deactivated_member_is_refused(self):
        TeamMember.objects.filter(email=RAVI).update(active=False)
        self.refused({"Photographer": RAVI})

    def test_the_page_comes_back_with_the_other_choices_kept(self):
        response = self.refused({"Photographer": OLI, "Event Coordinator": VIC})
        rows = {r.task: r for r in response.context["rows"]}
        self.assertEqual(rows["Event Coordinator"].selected, VIC)
        self.assertIn("not eligible", rows["Photographer"].error)

    def test_a_valid_pick_alongside_an_invalid_one_is_not_half_applied(self):
        self.refused({"Photographer": OLI, "Event Coordinator": VIC})
        self.assertEqual(Task.objects.count(), 0)

    def test_a_pick_skips_the_check_only_if_it_passes_it_so_a_good_one_works_after_a_bad_one(self):
        self.refused({"Photographer": OLI})
        self.confirm({"Photographer": RAVI})
        self.assertEqual(self.task(Request.objects.get(), "Photographer").email, RAVI)


class NotForEveryoneTests(Base):
    def test_a_clubs_own_request_ignores_pick_fields_and_has_no_second_step(self):
        self.client.force_login(self.club)
        response = self.client.post(reverse("request-new"), {**self.form(), "pick:Photographer": RAVI})
        self.assertRedirects(response, reverse("request-new"))
        request_obj = Request.objects.get()
        self.assertEqual(request_obj.created_on_behalf_by, "")
        self.assertNotEqual(self.task(request_obj, "Photographer").email, "")

    def test_a_clubs_own_request_is_not_forced_onto_the_chosen_person(self):
        picks = set()
        for _ in range(12):
            Request.objects.all().delete()
            self.client.force_login(self.club)
            self.client.post(reverse("request-new"), {**self.form(), "pick:Photographer": VIC})
            picks.add(self.task(Request.objects.get(), "Photographer").email)
        self.assertNotEqual(picks, {VIC})

    def test_the_second_step_cannot_be_reached_by_anyone_but_staff(self):
        self.client.force_login(self.club)
        self.assertEqual(self.client.post(self.url, {**self.form(), "step": "confirm"}).status_code, 403)
        self.assertEqual(Request.objects.count(), 0)


class CalendarLoadTests(Base):
    def test_each_persons_calendar_is_asked_once_per_window_not_once_per_task(self):
        asked = []

        class Counting:
            def is_free(self, email, start, end):
                asked.append(email)
                return True

            def create_hold(self, **kw):
                pass

            def create_reminder(self, **kw):
                pass

        with mock.patch("ui.allocation.calendar_service", return_value=Counting()):
            self.step_one()
        self.assertEqual(len(asked), len(set(asked)), "someone was checked more than once")
