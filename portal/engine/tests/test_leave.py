"""
Out-of-work requests: asking, deciding, starting and ending by date, coming back
early, and the availability day-banking they share with the Admin's switch.

In-memory database and locmem mail: nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from core.constants import Availability, LeaveStatus
from core.models import LeaveRequest, TeamMember
from engine import leave as engine
from engine.workflow import run_deadline_check

from .factories import NEHA, build_world
from .test_meetings_and_pairing import accepted_coverage

POC = "poc@iimsirmaur.ac.in"


def today():
    return timezone.localdate()


class LeaveBase(TestCase):
    def setUp(self):
        build_world()
        self.neha = TeamMember.objects.get(email=NEHA)
        mail.outbox.clear()

    def ask(self, start=None, end=None, reason="Exams"):
        start = start or today() + timedelta(days=3)
        return engine.request_leave(self.neha, reason, start, end or start + timedelta(days=4))

    def refresh(self):
        self.neha.refresh_from_db()
        return self.neha


class RequestingTests(LeaveBase):
    def test_a_request_is_pending_and_changes_nothing_for_the_member(self):
        leave = self.ask()
        self.assertEqual(leave.status, LeaveStatus.PENDING)
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)

    def test_the_poc_is_emailed_with_the_dates_reason_and_open_tasks(self):
        accepted_coverage(roles_needed=["Content Writer"])  # gives Neha some work
        mail.outbox.clear()
        self.ask(reason="Semester exams")
        notice = next(m for m in mail.outbox if m.subject.startswith("[Out of work request]"))
        self.assertEqual(notice.to, [POC])
        self.assertIn("Semester exams", notice.body)
        self.assertIn("Neha", notice.subject)
        self.assertIn("never moved automatically", notice.body)

    def test_a_reason_is_required(self):
        with self.assertRaises(engine.LeaveError):
            self.ask(reason="   ")

    def test_dates_must_make_sense(self):
        with self.assertRaises(engine.LeaveError):
            self.ask(start=today() - timedelta(days=1))
        with self.assertRaises(engine.LeaveError):
            self.ask(start=today() + timedelta(days=5), end=today() + timedelta(days=2))
        self.assertEqual(LeaveRequest.objects.count(), 0)

    def test_starting_today_is_allowed(self):
        self.assertEqual(self.ask(start=today(), end=today()).status, LeaveStatus.PENDING)

    def test_only_one_open_request_at_a_time(self):
        self.ask()
        with self.assertRaises(engine.LeaveError):
            self.ask(start=today() + timedelta(days=20))

    def test_cannot_ask_while_already_out(self):
        engine.switch_availability(NEHA, Availability.OUT, POC)
        with self.assertRaises(engine.LeaveError):
            self.ask()

    def test_a_declined_or_withdrawn_request_lets_them_ask_again(self):
        first = self.ask()
        engine.cancel_leave(first, NEHA)
        self.assertEqual(self.ask().status, LeaveStatus.PENDING)


class DecidingTests(LeaveBase):
    def test_approving_a_leave_that_starts_today_switches_them_out_now(self):
        leave = self.ask(start=today(), end=today() + timedelta(days=2))
        engine.decide_leave(leave, approve=True, actor=POC)
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.APPROVED)
        self.assertIsNotNone(leave.started_at)
        self.assertEqual(self.refresh().availability, Availability.OUT)

    def test_approving_a_future_leave_leaves_them_on_work_until_the_day(self):
        leave = self.ask()
        engine.decide_leave(leave, approve=True, actor=POC)
        leave.refresh_from_db()
        self.assertIsNone(leave.started_at)
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)

    def test_the_member_is_emailed_the_decision_and_the_note(self):
        leave = self.ask()
        mail.outbox.clear()
        engine.decide_leave(leave, approve=True, actor=POC, note="Good luck")
        approved = mail.outbox[0]
        self.assertEqual(approved.to, [NEHA])
        self.assertTrue(approved.subject.startswith("[Out of work approved]"))
        self.assertIn("Good luck", approved.body)
        self.assertIn("automatically", approved.body)

    def test_declining_changes_nothing_and_says_so(self):
        leave = self.ask(start=today(), end=today() + timedelta(days=2))
        mail.outbox.clear()
        engine.decide_leave(leave, approve=False, actor=POC, note="Busy week")
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.REJECTED)
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)
        self.assertTrue(mail.outbox[0].subject.startswith("[Out of work declined]"))
        self.assertIn("Busy week", mail.outbox[0].body)

    def test_a_request_can_only_be_decided_once(self):
        leave = self.ask()
        engine.decide_leave(leave, approve=False, actor=POC)
        with self.assertRaises(engine.LeaveError):
            engine.decide_leave(leave, approve=True, actor=POC)

    def test_dates_that_have_already_passed_cannot_be_approved(self):
        leave = self.ask()
        LeaveRequest.objects.filter(pk=leave.pk).update(
            start_date=today() - timedelta(days=5), end_date=today() - timedelta(days=1)
        )
        leave.refresh_from_db()
        with self.assertRaises(engine.LeaveError):
            engine.decide_leave(leave, approve=True, actor=POC)

    def test_their_open_tasks_are_never_moved(self):
        request = accepted_coverage(roles_needed=["Content Writer"])
        from core.models import TaskType

        TaskType.objects.get(task="Content Writer")  # exists in the fixture
        before = {t.pk: t.email for t in request.tasks.all()}
        leave = self.ask(start=today(), end=today() + timedelta(days=2))
        engine.decide_leave(leave, approve=True, actor=POC)
        self.assertEqual({t.pk: t.email for t in request.tasks.all()}, before)

    def test_open_tasks_lists_only_live_unfinished_work(self):
        TeamMember.objects.filter(email="asha@iimsirmaur.ac.in").update(points=100)  # so Neha coordinates
        request = accepted_coverage(roles_needed=[])
        tasks = engine.open_tasks_for(self.neha)
        self.assertEqual({t.task for t in tasks}, {"Event Coordinator"})
        request.tasks.filter(email=NEHA).update(status="DONE")
        self.assertEqual(engine.open_tasks_for(self.neha), [])


class WithdrawingAndReturningTests(LeaveBase):
    def test_a_pending_or_not_yet_started_request_can_be_withdrawn(self):
        pending = self.ask()
        engine.cancel_leave(pending, NEHA)
        pending.refresh_from_db()
        self.assertEqual(pending.status, LeaveStatus.CANCELLED)

        scheduled = self.ask()
        engine.decide_leave(scheduled, approve=True, actor=POC)
        engine.cancel_leave(scheduled, NEHA)
        scheduled.refresh_from_db()
        self.assertEqual(scheduled.status, LeaveStatus.CANCELLED)

    def test_a_leave_that_has_started_is_ended_by_coming_back_not_withdrawing(self):
        leave = self.ask(start=today(), end=today() + timedelta(days=2))
        engine.decide_leave(leave, approve=True, actor=POC)
        with self.assertRaises(engine.LeaveError):
            engine.cancel_leave(leave, NEHA)

    def test_coming_back_early_ends_the_leave(self):
        leave = self.ask(start=today(), end=today() + timedelta(days=9))
        engine.decide_leave(leave, approve=True, actor=POC)
        engine.return_now(self.refresh(), NEHA)
        leave.refresh_from_db()
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)
        self.assertEqual(leave.status, LeaveStatus.ENDED)
        self.assertIsNotNone(leave.ended_at)

    def test_you_cannot_come_back_when_you_are_not_out(self):
        with self.assertRaises(engine.LeaveError):
            engine.return_now(self.neha, NEHA)

    def test_the_admin_bringing_someone_back_also_closes_their_leave(self):
        leave = self.ask(start=today(), end=today() + timedelta(days=9))
        engine.decide_leave(leave, approve=True, actor=POC)
        engine.switch_availability(NEHA, Availability.AVAILABLE, POC)
        leave.refresh_from_db()
        self.assertEqual(leave.status, LeaveStatus.ENDED)

    def test_days_are_banked_whichever_way_the_switch_is_made(self):
        engine.switch_availability(NEHA, Availability.OUT, POC)
        TeamMember.objects.filter(pk=self.neha.pk).update(
            availability_changed_at=timezone.now() - timedelta(days=2)
        )
        engine.switch_availability(NEHA, Availability.AVAILABLE, NEHA)
        self.assertAlmostEqual(self.refresh().out_days, 2.0, delta=0.1)


class SweepTests(LeaveBase):
    def approved(self, start, end, **extra):
        return LeaveRequest.objects.create(
            member=self.neha, reason="Trip", start_date=start, end_date=end, status=LeaveStatus.APPROVED, **extra
        )

    def test_an_approved_leave_starts_on_its_first_day(self):
        leave = self.approved(today(), today() + timedelta(days=3))
        result = engine.run_leave_sweep()
        leave.refresh_from_db()
        self.assertEqual(result, {"started": 1, "ended": 0})
        self.assertEqual(self.refresh().availability, Availability.OUT)
        self.assertIsNotNone(leave.started_at)

    def test_it_does_not_start_early(self):
        self.approved(today() + timedelta(days=2), today() + timedelta(days=5))
        self.assertEqual(engine.run_leave_sweep(), {"started": 0, "ended": 0})
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)

    def test_back_on_work_the_day_after_the_last_day(self):
        leave = self.approved(today() - timedelta(days=3), today() - timedelta(days=1))
        engine.switch_availability(NEHA, Availability.OUT, "engine")
        LeaveRequest.objects.filter(pk=leave.pk).update(started_at=timezone.now() - timedelta(days=3))
        result = engine.run_leave_sweep()
        leave.refresh_from_db()
        self.assertEqual(result["ended"], 1)
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)
        self.assertEqual(leave.status, LeaveStatus.ENDED)

    def test_the_last_day_itself_is_still_out(self):
        leave = self.approved(today() - timedelta(days=2), today())
        engine.switch_availability(NEHA, Availability.OUT, "engine")
        LeaveRequest.objects.filter(pk=leave.pk).update(started_at=timezone.now())
        self.assertEqual(engine.run_leave_sweep()["ended"], 0)
        self.assertEqual(self.refresh().availability, Availability.OUT)

    def test_a_leave_the_sweep_never_got_to_start_simply_lapses(self):
        leave = self.approved(today() - timedelta(days=5), today() - timedelta(days=2))
        result = engine.run_leave_sweep()
        leave.refresh_from_db()
        self.assertEqual(result, {"started": 0, "ended": 0})
        self.assertEqual(leave.status, LeaveStatus.ENDED)
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)

    def test_it_is_idempotent(self):
        self.approved(today(), today() + timedelta(days=3))
        engine.run_leave_sweep()
        self.assertEqual(engine.run_leave_sweep(), {"started": 0, "ended": 0})

    def test_someone_already_back_by_hand_is_not_pushed_out_again_at_the_end(self):
        leave = self.approved(today() - timedelta(days=3), today() - timedelta(days=1))
        LeaveRequest.objects.filter(pk=leave.pk).update(started_at=timezone.now() - timedelta(days=3))
        engine.run_leave_sweep()  # they are On work already: it just closes the record
        self.assertEqual(self.refresh().availability, Availability.AVAILABLE)

    def test_the_hourly_deadline_sweep_runs_it(self):
        self.approved(today(), today() + timedelta(days=3))
        run_deadline_check()
        self.assertEqual(self.refresh().availability, Availability.OUT)
