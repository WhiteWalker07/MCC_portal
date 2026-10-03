"""
`manage.py reset_event_data`: preview by default, a typed confirmation, a mandatory
backup, and exactly the documented scope (nothing more is deleted).

In-memory test database and locmem mail. The real backup writes a copy of the
SQLite *file*, which doesn't exist for the in-memory test database, so the backup
step is replaced; one test checks that a failing backup stops everything.
"""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone

from core.models import (
    ActivityLog, Committee, LeaveRequest, Meeting, MeetingInvite, Request, SubEvent, Task, TeamMember,
)
from engine.tests.factories import COMMITTEE_EMAIL, NEHA, build_world, coverage_request
from engine.workflow import process_new_request

CMD = "core.management.commands.reset_event_data.Command"


def run(*args, typed=None):
    out = StringIO()
    with mock.patch(f"{CMD}._backup", return_value=Path("fake-backup.sqlite3")) as backup:
        if typed is None:
            call_command("reset_event_data", *args, stdout=out)
        else:
            with mock.patch("builtins.input", return_value=typed):
                call_command("reset_event_data", *args, stdout=out)
    return out.getvalue(), backup


class Base(TestCase):
    def setUp(self):
        build_world()
        from datetime import timedelta

        request = coverage_request(starts_in=timedelta(days=5))
        process_new_request(request)
        SubEvent.objects.create(
            request=request, name="Talk", start=timezone.now() + timedelta(days=5),
            end=timezone.now() + timedelta(days=5, hours=1),
        )
        TeamMember.objects.filter(email=NEHA).update(points=40, yellow_strikes=2, red_strikes=1)
        member = TeamMember.objects.get(email=NEHA)
        meeting = Meeting.objects.create(
            title="Sync", start=timezone.now() + timedelta(days=2),
            end=timezone.now() + timedelta(days=2, hours=1), called_by="poc@iimsirmaur.ac.in",
        )
        MeetingInvite.objects.create(meeting=meeting, member=member)
        LeaveRequest.objects.create(
            member=member, reason="Trip", start_date=timezone.localdate(), end_date=timezone.localdate()
        )
        self.before = {m.__name__: m.objects.count() for m in (Meeting, MeetingInvite, LeaveRequest, TeamMember, Committee)}


class PreviewTests(Base):
    def test_without_apply_nothing_changes_and_no_backup_is_made(self):
        out, backup = run()
        self.assertIn("Preview only", out)
        self.assertEqual(Request.objects.count(), 1)
        self.assertTrue(Task.objects.exists())
        self.assertEqual(TeamMember.objects.get(email=NEHA).points, 40)
        backup.assert_not_called()

    def test_the_preview_reports_the_counts(self):
        out, _ = run()
        self.assertIn("delete 1 request(s)", out)
        self.assertIn("1 sub-event(s)", out)
        self.assertIn("zero the points and strikes of 1 team member(s)", out)
        self.assertIn("restart the request-ID counter of 1 committee(s)", out)


class ConfirmationTests(Base):
    def test_anything_but_the_word_cancels(self):
        for typed in ("", "yes", "reset", "no"):
            out, backup = run("--apply", typed=typed)
            self.assertIn("Cancelled", out, typed)
            backup.assert_not_called()
        self.assertTrue(Request.objects.exists())

    def test_the_word_proceeds(self):
        out, backup = run("--apply", typed="RESET")
        self.assertIn("Done", out)
        backup.assert_called_once()
        self.assertFalse(Request.objects.exists())

    def test_yes_skips_the_prompt_for_scripts(self):
        with mock.patch("builtins.input", side_effect=AssertionError("must not prompt")):
            run("--apply", "--yes")
        self.assertFalse(Request.objects.exists())


class ScopeTests(Base):
    def apply(self):
        return run("--apply", "--yes")

    def test_requests_tasks_and_sub_events_are_deleted(self):
        self.apply()
        self.assertEqual((Request.objects.count(), Task.objects.count(), SubEvent.objects.count()), (0, 0, 0))

    def test_every_committees_id_counter_restarts_so_the_next_request_is_number_one(self):
        self.assertGreater(Committee.objects.get(email=COMMITTEE_EMAIL).last_seq, 0)
        self.apply()
        self.assertEqual(Committee.objects.get(email=COMMITTEE_EMAIL).last_seq, 0)
        from datetime import timedelta

        again = coverage_request(starts_in=timedelta(days=5))
        process_new_request(again)
        again.refresh_from_db()
        self.assertEqual(again.ref_code, "MKTG_1")

    def test_points_and_both_colours_of_strikes_are_zeroed(self):
        self.apply()
        neha = TeamMember.objects.get(email=NEHA)
        self.assertEqual((neha.points, neha.yellow_strikes, neha.red_strikes), (0, 0, 0))

    def test_everything_else_is_left_alone(self):
        self.apply()
        after = {m.__name__: m.objects.count() for m in (Meeting, MeetingInvite, LeaveRequest, TeamMember, Committee)}
        self.assertEqual(after, self.before)

    def test_the_activity_log_is_kept_and_notes_the_reset(self):
        entries_before = ActivityLog.objects.count()
        self.apply()
        self.assertGreaterEqual(ActivityLog.objects.count(), entries_before + 1)
        entry = ActivityLog.objects.filter(event="event-data-reset").get()
        self.assertIn("Deleted 1 request(s)", entry.detail)
        self.assertIn("fake-backup.sqlite3", entry.detail)
        # Old entries survive; they just no longer point at a deleted request.
        self.assertTrue(ActivityLog.objects.filter(event="created").exists())
        self.assertFalse(ActivityLog.objects.filter(request__isnull=False).exists())

    def test_running_it_twice_is_harmless(self):
        self.apply()
        out, backup = self.apply()
        self.assertIn("Nothing to reset", out)
        backup.assert_not_called()


class BackupIsMandatoryTests(Base):
    def test_if_the_backup_fails_nothing_is_changed(self):
        with mock.patch(f"{CMD}._backup", side_effect=CommandError("disk full")):
            with self.assertRaises(CommandError):
                call_command("reset_event_data", "--apply", "--yes", stdout=StringIO())
        self.assertTrue(Request.objects.exists())
        self.assertEqual(TeamMember.objects.get(email=NEHA).points, 40)
        self.assertEqual(Committee.objects.get(email=COMMITTEE_EMAIL).last_seq, 1)

    def test_the_real_backup_refuses_when_there_is_no_database_file(self):
        # The in-memory test database has no file to copy, which is exactly the case
        # where it must refuse rather than carry on unprotected.
        from core.management.commands.reset_event_data import Command

        with self.assertRaises(CommandError) as ctx:
            Command._backup()
        self.assertIn("without a backup", str(ctx.exception))
        self.assertTrue(Request.objects.exists())

    def test_the_backup_uses_a_name_the_nightly_pruning_ignores(self):
        import tempfile

        from django.conf import settings
        from django.test import override_settings

        from core.management.commands.reset_event_data import Command

        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "mcc.sqlite3"
            db.write_bytes(b"")
            databases = {"default": {**settings.DATABASES["default"], "NAME": str(db)}}
            with override_settings(BACKUP_DIR=tmp, DATABASES=databases):
                with mock.patch("core.management.commands.reset_event_data.BackupCommand._snapshot") as snap:
                    path = Command._backup()
        snap.assert_called_once()
        self.assertTrue(path.name.startswith("mcc-pre-reset-"))
        self.assertFalse(path.name.startswith(("mcc-daily-", "mcc-weekly-")))
