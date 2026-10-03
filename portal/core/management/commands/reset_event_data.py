"""
Wipe all event data and zero the scoreboard, to start a fresh term from a clean slate.

    python manage.py reset_event_data                  # preview only: counts, changes nothing
    python manage.py reset_event_data --apply          # back up, ask for 'RESET', then do it
    python manage.py reset_event_data --apply --yes    # same, without the typed confirmation

What it removes:
  * every request (Coverage and Post) together with its tasks and sub-events;
  * each committee's request-ID counter, so new IDs start again at SPT_1, MKTG_1 ...
  * every team member's points, and their yellow and red strikes.

What it leaves alone: the team roster itself, committees, task types, platforms,
slots, portal settings and the point scheme, meetings and attendance, out-of-work
requests, and the activity log (rows about deleted requests simply lose their link
to them; the reference code stays in the text).

A command rather than a button on purpose, and with a preview by default: this
cannot be undone from inside the portal. Before it deletes anything it writes a
full snapshot of the database next to the nightly backups, under a name that the
nightly pruning never touches (`mcc-pre-reset-<time>.sqlite3`); if that snapshot
cannot be written, nothing is changed. To undo, stop the service and copy that file
over the database.

Things it cannot undo: emails already sent, and calendar entries already created
in members' Google Calendars.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Q

from core.activity import log_activity
from core.models import Committee, Request, SubEvent, Task, TeamMember

from .backup_db import Command as BackupCommand

CONFIRM_WORD = "RESET"


class Command(BaseCommand):
    help = "Delete all requests/tasks/sub-events, restart request IDs, and zero points and strikes."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Actually do it. Without this the command only shows what it would delete.",
        )
        parser.add_argument(
            "--yes",
            action="store_true",
            help=f"With --apply, skip the typed '{CONFIRM_WORD}' confirmation (for scripts).",
        )

    def handle(self, *args, **options):
        counts = self._counts()
        self._show(counts)

        if not options["apply"]:
            self.stdout.write(self.style.WARNING("Preview only: nothing was changed. Re-run with --apply to do it."))
            return

        if not any(counts.values()):
            self.stdout.write("Nothing to reset.")
            return

        if not options["yes"]:
            self.stdout.write(self.style.WARNING("This cannot be undone from inside the portal."))
            typed = input(f"Type {CONFIRM_WORD} to continue: ").strip()
            if typed != CONFIRM_WORD:
                self.stdout.write("Cancelled: nothing was changed.")
                return

        # The snapshot comes first and is a hard requirement: no backup, no reset.
        snapshot = self._backup()
        self.stdout.write(self.style.SUCCESS(f"Backed up to {snapshot}"))

        with transaction.atomic():
            Request.objects.all().delete()  # cascades to tasks and sub-events
            Committee.objects.exclude(last_seq=0).update(last_seq=0)
            self._members_with_scores().update(points=0, yellow_strikes=0, red_strikes=0)
            log_activity(
                "event-data-reset",
                actor="admin (reset_event_data command)",
                detail=(
                    f"Deleted {counts['requests']} request(s), {counts['tasks']} task(s), "
                    f"{counts['subevents']} sub-event(s); restarted {counts['counters']} ID counter(s); "
                    f"zeroed points/strikes for {counts['members']} member(s). Backup: {snapshot.name}"
                ),
            )

        self.stdout.write(self.style.SUCCESS("Done. Event data cleared; points and strikes are zero."))
        self.stdout.write(
            "Reminder: emails already sent and calendar entries already created are not undone. "
            f"To undo everything, stop the service and copy {snapshot} over the database."
        )

    # ── helpers ─────────────────────────────────────────────────────────────

    @staticmethod
    def _counts() -> dict:
        return {
            "requests": Request.objects.count(),
            "tasks": Task.objects.count(),
            "subevents": SubEvent.objects.count(),
            "counters": Committee.objects.exclude(last_seq=0).count(),
            "members": Command._members_with_scores().count(),
        }

    @staticmethod
    def _members_with_scores():
        """Members who have any points or strikes (the ones the reset actually changes)."""
        return TeamMember.objects.filter(~Q(points=0) | ~Q(yellow_strikes=0) | ~Q(red_strikes=0))

    def _show(self, counts: dict) -> None:
        self.stdout.write("This would:")
        self.stdout.write(
            f"  - delete {counts['requests']} request(s), with {counts['tasks']} task(s) "
            f"and {counts['subevents']} sub-event(s)"
        )
        self.stdout.write(f"  - restart the request-ID counter of {counts['counters']} committee(s) at 1")
        self.stdout.write(f"  - zero the points and strikes of {counts['members']} team member(s)")
        self.stdout.write("  It keeps: the roster, committees, settings, point scheme, meetings, leave requests, activity log.")

    @staticmethod
    def _backup() -> Path:
        """Snapshot the database under a name the nightly pruning never deletes."""
        source = Path(settings.DATABASES["default"]["NAME"])
        if not source.is_file():
            raise CommandError(f"Database not found at {source}: refusing to reset without a backup.")
        directory = Path(settings.BACKUP_DIR)
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / f"mcc-pre-reset-{datetime.now():%Y%m%d-%H%M%S}.sqlite3"
        try:
            BackupCommand._snapshot(source, destination)
        except Exception as exc:  # any failure to back up must stop the reset
            destination.unlink(missing_ok=True)
            raise CommandError(f"Could not write the backup ({exc}): nothing was changed.") from exc
        return destination
