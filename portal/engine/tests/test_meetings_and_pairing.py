"""
Engine behaviour for: the timed Event Coordinator, shooters editing their own
work, and team meetings (who can be called, the minutes-taker, attendance and
the automatic yellow strike).

Everything runs against the in-memory test database with Django's locmem mail
backend — nothing here can send a real email.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase
from django.utils import timezone

from core.config import get_points_scheme
from core.constants import Attendance, Availability, TaskStatus
from core.models import Meeting, MeetingInvite, Request, Task, TeamMember
from engine import meetings as engine
from engine.assignment import override_proposed_assignee, perform_swap
from engine.event_changes import apply_event_time_change
from engine.pipeline import coordinator_deadline, refresh_coordinator_deadline
from engine.points import overdue_multiplier
from engine.workflow import _award_completion_points, process_new_request, run_deadline_check

from .factories import ASHA, NEHA, SUPERVISOR, FreeCalendar, build_world, coverage_request


def member(email, name, year=1, vertical="", secondary="", skills=(), points=0, **extra):
    return TeamMember.objects.create(
        email=email,
        name=name,
        year=year,
        campus="Permanent",
        vertical=vertical,
        secondary_vertical=secondary,
        skills=list(skills),
        points=points,
        **extra,
    )


def accepted_coverage(**overrides):
    """A Coverage request five days out: not gated, so it is accepted and its tasks CONFIRMED."""
    request = coverage_request(starts_in=timedelta(days=5), **overrides)
    process_new_request(request)
    request.refresh_from_db()
    mail.outbox.clear()
    return request


# ── The Event Coordinator is timed ──────────────────────────────────────────


class CoordinatorDeadlineTests(TestCase):
    def setUp(self):
        build_world()

    def test_due_twelve_hours_after_the_last_individual_task(self):
        request = accepted_coverage()  # Photographer + Photo Editor (due end + 24h)
        coordinator = request.tasks.get(task="Event Coordinator")
        editor = request.tasks.get(task="Photo Editor")
        self.assertEqual(editor.deadline, request.event_end + timedelta(hours=24))
        self.assertEqual(coordinator.deadline, editor.deadline + timedelta(hours=12))

    def test_with_no_other_tasks_it_is_the_event_end_plus_twelve_hours(self):
        request = accepted_coverage(roles_needed=[])
        coordinator = request.tasks.get(task="Event Coordinator")
        self.assertEqual(coordinator.deadline, request.event_end + timedelta(hours=12))

    def test_the_supervisor_still_has_no_deadline(self):
        request = accepted_coverage()
        self.assertIsNone(request.tasks.get(task="Task Supervisor").deadline)

    def test_it_follows_a_later_task_added_afterwards(self):
        request = accepted_coverage()
        Task.objects.create(
            request=request, req_type="Coverage", ref_code=request.ref_code, task="Video Editor",
            email=NEHA, member="Neha", status=TaskStatus.CONFIRMED,
            deadline=request.event_end + timedelta(hours=48),
        )
        refresh_coordinator_deadline(request)
        coordinator = request.tasks.get(task="Event Coordinator")
        self.assertEqual(coordinator.deadline, request.event_end + timedelta(hours=60))

    def test_a_finished_coordinator_keeps_the_deadline_it_was_judged_against(self):
        request = accepted_coverage()
        coordinator = request.tasks.get(task="Event Coordinator")
        coordinator.status = TaskStatus.DONE
        coordinator.save()
        before = coordinator.deadline
        Task.objects.create(
            request=request, req_type="Coverage", ref_code=request.ref_code, task="Video Editor",
            email=NEHA, member="Neha", status=TaskStatus.CONFIRMED,
            deadline=request.event_end + timedelta(hours=90),
        )
        refresh_coordinator_deadline(request)
        coordinator.refresh_from_db()
        self.assertEqual(coordinator.deadline, before)

    def test_a_time_change_moves_it(self):
        request = accepted_coverage()
        new_end = request.event_end + timedelta(hours=3)
        with mock.patch("engine.event_changes.calendar_service", return_value=FreeCalendar()):
            apply_event_time_change(request, request.event_start, new_end, "club")
        coordinator = request.tasks.get(task="Event Coordinator")
        self.assertEqual(coordinator.deadline, new_end + timedelta(hours=36))

    def test_coordinator_deadline_helper_handles_no_event_end(self):
        request = Request(type="Coverage", event_end=None)
        self.assertIsNone(coordinator_deadline(request, [None]))


class CoordinatorPenaltyTests(TestCase):
    def setUp(self):
        build_world()
        self.scheme = get_points_scheme()

    def test_curve_matches_the_late_penalty_from_the_deadline(self):
        self.assertEqual(overdue_multiplier(-3, self.scheme), 1.0)
        self.assertEqual(overdue_multiplier(0, self.scheme), 1.0)
        self.assertAlmostEqual(overdue_multiplier(0.5, self.scheme), 0.7)
        self.assertAlmostEqual(overdue_multiplier(5.9, self.scheme), 0.7)
        self.assertAlmostEqual(overdue_multiplier(6, self.scheme), 0.6)
        self.assertAlmostEqual(overdue_multiplier(12, self.scheme), 0.5)
        self.assertEqual(overdue_multiplier(500, self.scheme), 0.0)

    def finish_coordinator(self, offset: timedelta):
        request = accepted_coverage()
        coordinator = request.tasks.get(task="Event Coordinator")
        holder = TeamMember.objects.get(email=coordinator.email)
        before = holder.points
        coordinator.status = TaskStatus.DONE
        coordinator.completed_at = coordinator.deadline + offset
        coordinator.save()
        _award_completion_points(coordinator, request)
        holder.refresh_from_db()
        return holder.points - before

    def test_on_time_is_the_flat_twenty_with_no_bonus(self):
        self.assertEqual(self.finish_coordinator(timedelta(hours=-30)), 20)
        self.assertEqual(self.finish_coordinator(timedelta(0)), 20)

    def test_just_late_loses_thirty_percent(self):
        self.assertEqual(self.finish_coordinator(timedelta(hours=3)), 14)

    def test_six_hours_late_loses_forty_percent(self):
        self.assertEqual(self.finish_coordinator(timedelta(hours=6)), 12)

    def test_very_late_earns_nothing(self):
        self.assertEqual(self.finish_coordinator(timedelta(hours=100)), 0)

    def test_the_sweep_marks_an_overdue_coordinator_late_and_emails_the_usual_people(self):
        request = accepted_coverage()
        coordinator = request.tasks.get(task="Event Coordinator")
        coordinator.deadline = timezone.now() - timedelta(hours=1)
        coordinator.save()
        # The other tasks are not overdue, so the coordinator is the only one swept.
        result = run_deadline_check()
        self.assertEqual(result["late"], 1)
        coordinator.refresh_from_db()
        self.assertEqual(coordinator.status, TaskStatus.LATE)
        notice = next(m for m in mail.outbox if "[Late]" in m.subject)
        self.assertIn("Event Coordinator", notice.subject)
        self.assertIn(coordinator.email, notice.to)
        self.assertIn(request.supervisor_email, notice.to)

    def test_the_supervisor_is_never_swept(self):
        request = accepted_coverage()
        supervision = request.tasks.get(task="Task Supervisor")
        Task.objects.filter(pk=supervision.pk).update(deadline=timezone.now() - timedelta(days=1))
        run_deadline_check()
        supervision.refresh_from_db()
        self.assertEqual(supervision.status, TaskStatus.CONFIRMED)


# ── Each shooter edits their own work ───────────────────────────────────────


class PairedEditorTests(TestCase):
    def setUp(self):
        build_world()
        # Ravi shoots but can't edit photos; Asha (the fixture's editor) already has more points.
        TeamMember.objects.filter(email=ASHA).update(points=100)
        self.ravi = member("ravi@iimsirmaur.ac.in", "Ravi", vertical="Photography", skills=["Photography"])

    def test_the_photographer_also_gets_the_photo_editing_even_without_the_skill(self):
        request = accepted_coverage()
        photographer = request.tasks.get(task="Photographer")
        editor = request.tasks.get(task="Photo Editor")
        self.assertEqual(photographer.email, self.ravi.email)
        self.assertEqual(editor.email, self.ravi.email)
        self.assertEqual(editor.paired_task_id, photographer.pk)

    def test_videographer_and_video_editor_are_paired_too(self):
        from core.models import TaskType

        TaskType.objects.create(
            task="Videographer", required_skill="Videography", points=8, sla_hours=0,
            at_event=True, requestable=True, internal_assignable=True, vertical="Videography",
        )
        TaskType.objects.create(
            task="Video Editor", required_skill="Video Editing", points=5, sla_hours=48,
            at_event=False, requestable=False, internal_assignable=True, vertical="Videography",
        )
        member("vic@iimsirmaur.ac.in", "Vic", vertical="Videography", skills=["Videography"])
        request = accepted_coverage(roles_needed=["Videographer"])
        video = request.tasks.get(task="Videographer")
        edit = request.tasks.get(task="Video Editor")
        self.assertEqual((video.email, edit.email), ("vic@iimsirmaur.ac.in",) * 2)
        self.assertEqual(edit.paired_task_id, video.pk)

    def test_an_unfilled_shooter_leaves_the_editor_to_the_usual_auto_pick(self):
        TeamMember.objects.update(availability=Availability.OUT)
        TeamMember.objects.filter(email=ASHA).update(availability=Availability.AVAILABLE)
        # Nobody can shoot except Asha, who is busy? Make the shoot role unfillable instead.
        TeamMember.objects.filter(email=ASHA).update(skills=["Photo Editing"])
        request = accepted_coverage()
        photographer = request.tasks.get(task="Photographer")
        editor = request.tasks.get(task="Photo Editor")
        self.assertEqual(photographer.status, TaskStatus.UNFILLED)
        self.assertEqual(editor.email, ASHA)

    def test_reassigning_the_shooter_moves_the_editing_with_them(self):
        request = accepted_coverage()
        photographer = request.tasks.get(task="Photographer")
        neha = TeamMember.objects.get(email=NEHA)
        perform_swap(photographer, neha, request)
        self.assertEqual(request.tasks.get(task="Photo Editor").email, NEHA)

    def test_a_finished_editing_task_is_left_with_whoever_did_it(self):
        request = accepted_coverage()
        editor = request.tasks.get(task="Photo Editor")
        editor.status = TaskStatus.DONE
        editor.save()
        perform_swap(request.tasks.get(task="Photographer"), TeamMember.objects.get(email=NEHA), request)
        editor.refresh_from_db()
        self.assertEqual(editor.email, self.ravi.email)

    def test_an_editor_deliberately_given_to_someone_else_stays_put(self):
        request = accepted_coverage()
        editor = request.tasks.get(task="Photo Editor")
        editor.email, editor.member = ASHA, "Asha"
        editor.save()
        perform_swap(request.tasks.get(task="Photographer"), TeamMember.objects.get(email=NEHA), request)
        editor.refresh_from_db()
        self.assertEqual(editor.email, ASHA)

    def test_the_pairing_is_broken_by_reassigning_only_the_editor(self):
        request = accepted_coverage()
        editor = request.tasks.get(task="Photo Editor")
        perform_swap(editor, TeamMember.objects.get(email=ASHA), request)
        self.assertEqual(request.tasks.get(task="Photographer").email, self.ravi.email)

    def test_the_pre_approval_override_moves_the_editor_too(self):
        request = coverage_request(starts_in=timedelta(hours=5))  # short notice: held for approval
        process_new_request(request)
        request.refresh_from_db()
        self.assertEqual(request.status, "Pending for POC approval")
        photographer = request.tasks.get(task="Photographer")
        override_proposed_assignee(photographer, TeamMember.objects.get(email=NEHA))
        self.assertEqual(request.tasks.get(task="Photo Editor").email, NEHA)

    def test_reassigning_the_shooter_does_not_email_the_club_about_the_editor(self):
        request = accepted_coverage()
        perform_swap(request.tasks.get(task="Photographer"), TeamMember.objects.get(email=NEHA), request)
        club_mails = [m for m in mail.outbox if "[Team update]" in m.subject]
        self.assertEqual(len(club_mails), 1)
        self.assertIn("Photographer", club_mails[0].body)


# ── Meetings: who can be called ─────────────────────────────────────────────


def in_two_days(hours=0):
    return timezone.now() + timedelta(days=2, hours=hours)


class MeetingBase(TestCase):
    def setUp(self):
        build_world()
        self.asha = TeamMember.objects.get(email=ASHA)
        self.neha = TeamMember.objects.get(email=NEHA)
        self.sup = TeamMember.objects.get(email=SUPERVISOR)
        self.ravi = member("ravi@iimsirmaur.ac.in", "Ravi", vertical="Photography", points=5)
        self.oli = member("oli@iimsirmaur.ac.in", "Oli", vertical="Photography", availability=Availability.OUT)
        self.ina = member("ina@iimsirmaur.ac.in", "Ina", vertical="Photography", active=False)
        mail.outbox.clear()

    def call(self, invitees=None, **overrides):
        values = dict(
            title="Weekly sync", start=in_two_days(), end=in_two_days(1), venue="Room 4", agenda="Roster",
            called_by="poc@iimsirmaur.ac.in",
            invitees=invitees if invitees is not None else [self.asha, self.neha, self.ravi],
            wants_mom=False,
        )
        values.update(overrides)
        return engine.create_meeting(**values)


class InviteeRuleTests(MeetingBase):
    def test_people_out_of_work_or_inactive_cannot_be_called(self):
        emails = {m.email for m in engine.invitable_members()}
        self.assertNotIn(self.oli.email, emails)
        self.assertNotIn(self.ina.email, emails)
        self.assertIn(self.sup.email, emails)  # second-years can be called; only the MOM role excludes them

    def test_whole_team_mode(self):
        self.assertEqual(
            {m.email for m in engine.resolve_invitees(engine.INVITE_ALL)},
            {m.email for m in engine.invitable_members()},
        )

    def test_vertical_mode_matches_primary_or_secondary_and_skips_out_of_work(self):
        secondary = member("sec@iimsirmaur.ac.in", "Sec", vertical="Content Writing", secondary="Photography")
        found = {m.email for m in engine.resolve_invitees(engine.INVITE_VERTICALS, verticals=["Photography"])}
        self.assertEqual(found, {ASHA, self.ravi.email, secondary.email})
        self.assertNotIn(self.oli.email, found)

    def test_people_mode_drops_anyone_out_of_work(self):
        found = engine.resolve_invitees(engine.INVITE_PEOPLE, emails=[ASHA, self.oli.email, "nobody@x.in"])
        self.assertEqual([m.email for m in found], [ASHA])

    def test_creating_a_meeting_rejects_someone_out_of_work(self):
        with self.assertRaises(engine.MeetingError):
            self.call(invitees=[self.asha, self.oli])
        self.assertEqual(Meeting.objects.count(), 0)
        self.assertEqual(mail.outbox, [])

    def test_a_meeting_needs_someone_invited_and_a_sensible_time(self):
        with self.assertRaises(engine.MeetingError):
            self.call(invitees=[])
        with self.assertRaises(engine.MeetingError):
            self.call(start=in_two_days(2), end=in_two_days(1))


class MomChoiceTests(MeetingBase):
    def test_suggests_the_first_year_with_the_fewest_points_never_a_second_year(self):
        TeamMember.objects.filter(pk=self.sup.pk).update(points=-50)
        self.sup.refresh_from_db()
        pick = engine.choose_mom([self.sup, self.ravi, self.asha, self.neha])
        self.assertEqual(pick.email, ASHA)  # 0 points, and 'Asha' sorts before 'Neha'

    def test_nobody_when_only_second_years_are_invited(self):
        self.assertIsNone(engine.choose_mom([self.sup]))

    def test_the_caller_can_name_someone_else_among_the_invited_first_years(self):
        meeting = self.call(wants_mom=True, mom=self.ravi)
        self.assertEqual(meeting.mom_email, self.ravi.email)

    def test_a_minutes_taker_must_be_invited_and_a_first_year(self):
        with self.assertRaises(engine.MeetingError):
            self.call(invitees=[self.asha, self.neha], wants_mom=True, mom=self.ravi)  # not invited
        with self.assertRaises(engine.MeetingError):
            self.call(invitees=[self.asha, self.sup], wants_mom=True, mom=self.sup)  # a second-year
        with self.assertRaises(engine.MeetingError):
            self.call(invitees=[self.sup], wants_mom=True)  # nobody suitable to auto-pick

    def test_auto_picked_when_ticked_and_not_named(self):
        meeting = self.call(wants_mom=True)
        self.assertEqual(meeting.mom_email, ASHA)

    def test_nobody_is_assigned_when_the_box_is_not_ticked(self):
        self.assertEqual(self.call().mom_email, "")


class MeetingEmailTests(MeetingBase):
    def test_invitation_goes_to_everyone_in_one_threaded_message_with_the_disclaimer(self):
        meeting = self.call()
        self.assertEqual(len(mail.outbox), 1)
        invite = mail.outbox[0]
        self.assertTrue(invite.subject.startswith("[Meeting] Weekly sync"))
        self.assertEqual(set(invite.to), {ASHA, NEHA, self.ravi.email})
        self.assertIn("Room 4", invite.body)
        self.assertIn("Roster", invite.body)
        self.assertIn("system-generated", invite.body)
        self.assertEqual(invite.extra_headers["Message-ID"], f"<MEET_{meeting.pk}@{invite.extra_headers['Message-ID'].split('@')[1]}")

    def test_the_minutes_taker_gets_their_own_email_and_is_named_in_the_invite(self):
        self.call(venue="", wants_mom=True, mom=self.ravi)
        subjects = [m.subject for m in mail.outbox]
        self.assertTrue(any(s.startswith("[Meeting]") for s in subjects))
        duty = next(m for m in mail.outbox if m.subject.startswith("[MOM duty]"))
        self.assertEqual(duty.to, [self.ravi.email])
        self.assertIn("no points", duty.body)
        invite = next(m for m in mail.outbox if m.subject.startswith("[Meeting]"))
        self.assertIn("Ravi is booking it", invite.body)
        self.assertIn("Message-ID", invite.extra_headers)
        self.assertIn("In-Reply-To", duty.extra_headers)

    def test_editing_tells_those_who_stay_who_were_added_and_who_were_dropped(self):
        meeting = self.call()
        mail.outbox.clear()
        engine.update_meeting(
            meeting, title="Weekly sync", start=meeting.start, end=meeting.end, venue="Room 9",
            agenda="Roster", invitees=[self.asha, self.sup], wants_mom=False, actor="poc@iimsirmaur.ac.in",
        )
        updated = next(m for m in mail.outbox if "you are no longer invited" not in m.subject)
        self.assertTrue(updated.subject.startswith("[Meeting updated]"))
        self.assertEqual(set(updated.to), {ASHA, SUPERVISOR})
        self.assertIn("Venue: Room 4 -> Room 9", updated.body)
        dropped = next(m for m in mail.outbox if "no longer invited" in m.subject)
        self.assertEqual(set(dropped.to), {NEHA, self.ravi.email})
        self.assertEqual(
            set(meeting.invites.values_list("member__email", flat=True)), {ASHA, SUPERVISOR}
        )

    def test_moving_the_time_says_the_old_calendar_entry_is_stale(self):
        meeting = self.call()
        mail.outbox.clear()
        engine.update_meeting(
            meeting, title="Weekly sync", start=in_two_days(3), end=in_two_days(4), venue="Room 4",
            agenda="Roster", invitees=[self.asha], wants_mom=False, actor="poc@iimsirmaur.ac.in",
        )
        self.assertIn("calendar", mail.outbox[0].body)

    def test_changing_the_minutes_taker_informs_the_new_one_and_releases_the_old(self):
        meeting = self.call(wants_mom=True, mom=self.ravi)
        mail.outbox.clear()
        engine.set_mom(meeting, self.neha, actor="poc@iimsirmaur.ac.in")
        meeting.refresh_from_db()
        self.assertEqual(meeting.mom_email, NEHA)
        by_subject = {m.subject: m for m in mail.outbox}
        self.assertEqual(by_subject["[MOM duty] Weekly sync"].to, [NEHA])
        self.assertEqual(by_subject["[MOM duty] Weekly sync — released"].to, [self.ravi.email])

    def test_dropping_the_minutes_taker_from_the_list_picks_a_replacement(self):
        meeting = self.call(wants_mom=True, mom=self.ravi)
        engine.update_meeting(
            meeting, title="Weekly sync", start=meeting.start, end=meeting.end, venue="Room 4", agenda="",
            invitees=[self.neha, self.asha], wants_mom=True, actor="poc@iimsirmaur.ac.in",
        )
        meeting.refresh_from_db()
        self.assertEqual(meeting.mom_email, ASHA)

    def test_cancelling_tells_everyone_and_blocks_further_changes(self):
        meeting = self.call()
        mail.outbox.clear()
        engine.cancel_meeting(meeting, actor="poc@iimsirmaur.ac.in")
        meeting.refresh_from_db()
        self.assertTrue(meeting.is_cancelled)
        cancelled = mail.outbox[0]
        self.assertTrue(cancelled.subject.startswith("[Meeting cancelled]"))
        self.assertEqual(set(cancelled.to), {ASHA, NEHA, self.ravi.email})
        with self.assertRaises(engine.MeetingError):
            engine.cancel_meeting(meeting, actor="poc@iimsirmaur.ac.in")

    def test_a_meeting_that_has_started_can_no_longer_be_changed(self):
        meeting = self.call()
        Meeting.objects.filter(pk=meeting.pk).update(start=timezone.now() - timedelta(minutes=5))
        meeting.refresh_from_db()
        with self.assertRaises(engine.MeetingError):
            engine.update_meeting(
                meeting, title="x", start=in_two_days(), end=in_two_days(1), venue="", agenda="",
                invitees=[self.asha], wants_mom=False, actor="poc@iimsirmaur.ac.in",
            )


# ── Attendance and the automatic yellow strike ──────────────────────────────


class AttendanceTests(MeetingBase):
    def setUp(self):
        super().setUp()
        self.meeting = self.call()
        Meeting.objects.filter(pk=self.meeting.pk).update(
            start=timezone.now() - timedelta(minutes=30), end=timezone.now() + timedelta(minutes=30)
        )
        self.meeting.refresh_from_db()
        self.invite = self.meeting.invites.get(member=self.neha)

    def strikes(self):
        return TeamMember.objects.get(pk=self.neha.pk).yellow_strikes

    def mark(self, status):
        return engine.mark_attendance(
            MeetingInvite.objects.get(pk=self.invite.pk), status, actor="poc@iimsirmaur.ac.in"
        )

    def test_absent_gives_one_yellow_strike(self):
        self.mark(Attendance.ABSENT)
        self.assertEqual(self.strikes(), 1)
        self.assertEqual(TeamMember.objects.get(pk=self.neha.pk).red_strikes, 0)

    def test_saving_absent_again_never_double_strikes(self):
        self.mark(Attendance.ABSENT)
        self.mark(Attendance.ABSENT)
        self.assertEqual(self.strikes(), 1)

    def test_correcting_absent_takes_the_strike_back_off(self):
        self.mark(Attendance.ABSENT)
        self.mark(Attendance.PRESENT)
        self.assertEqual(self.strikes(), 0)
        self.assertFalse(MeetingInvite.objects.get(pk=self.invite.pk).strike_given)

    def test_going_absent_again_after_a_correction_strikes_again(self):
        self.mark(Attendance.ABSENT)
        self.mark(Attendance.EXCUSED)
        self.mark(Attendance.ABSENT)
        self.assertEqual(self.strikes(), 1)

    def test_late_and_excused_have_no_effect(self):
        self.mark(Attendance.LATE)
        self.mark(Attendance.EXCUSED)
        self.assertEqual(self.strikes(), 0)

    def test_a_strike_already_removed_by_hand_is_not_taken_twice(self):
        self.mark(Attendance.ABSENT)
        TeamMember.objects.filter(pk=self.neha.pk).update(yellow_strikes=0)  # the POC removed it
        self.mark(Attendance.PRESENT)
        self.assertEqual(self.strikes(), 0)  # never negative

    def test_it_does_not_disturb_strikes_the_member_already_had(self):
        TeamMember.objects.filter(pk=self.neha.pk).update(yellow_strikes=2)
        self.mark(Attendance.ABSENT)
        self.assertEqual(self.strikes(), 3)
        self.mark(Attendance.PRESENT)
        self.assertEqual(self.strikes(), 2)

    def test_it_is_recorded_in_the_activity_log(self):
        from core.models import ActivityLog

        self.mark(Attendance.ABSENT)
        entry = ActivityLog.objects.filter(event="attendance").first()
        self.assertIn("yellow strike given", entry.detail)

    def test_not_before_the_meeting_starts(self):
        Meeting.objects.filter(pk=self.meeting.pk).update(start=in_two_days(), end=in_two_days(1))
        with self.assertRaises(engine.MeetingError):
            self.mark(Attendance.ABSENT)
        self.assertEqual(self.strikes(), 0)

    def test_not_once_cancelled(self):
        Meeting.objects.filter(pk=self.meeting.pk).update(cancelled_at=timezone.now())
        with self.assertRaises(engine.MeetingError):
            self.mark(Attendance.ABSENT)

    def test_unknown_status_is_refused(self):
        with self.assertRaises(engine.MeetingError):
            self.mark("skipped")
