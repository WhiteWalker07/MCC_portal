"""
Back up the SQLite database.

This is a genuinely new responsibility. MongoDB Atlas used to handle backups for
this system; on a single lab PC nobody does unless we do. If the disk fails and
there is no copy, the committee's entire record of who did what is gone.

Uses SQLite's own online-backup API (`Connection.backup`), which is safe to run
against a live, in-use database under WAL — unlike copying the file, which can
capture a torn state. It needs no `sqlite3.exe` on PATH, which Windows lacks by
default.

    python manage.py backup_db
    python manage.py backup_db --weekly     # retained on the weekly schedule

Register nightly with Task Scheduler, and point BACKUP_DIR at a second drive or
a network share — a backup on the same disk as the database protects you from
almost nothing. See HANDOVER.md.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Write a consistent snapshot of the SQLite database to BACKUP_DIR."

    def add_arguments(self, parser):
        parser.add_argument(
            "--weekly",
            action="store_true",
            help="Tag this snapshot 'weekly' so it is kept under the longer retention.",
        )
        parser.add_argument(
            "--dir",
            default=None,
            help="Override the destination directory (defaults to settings.BACKUP_DIR).",
        )

    def handle(self, *args, **options):
        source = Path(settings.DATABASES["default"]["NAME"])
        if not source.is_file():
            raise CommandError(f"Database not found at {source}")

        destination_dir = Path(options["dir"]) if options["dir"] else Path(settings.BACKUP_DIR)
        destination_dir.mkdir(parents=True, exist_ok=True)

        tag = "weekly" if options["weekly"] else "daily"
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        destination = destination_dir / f"mcc-{tag}-{stamp}.sqlite3"

        self._snapshot(source, destination)
        size_mb = destination.stat().st_size / (1024 * 1024)
        self.stdout.write(self.style.SUCCESS(f"Wrote {destination} ({size_mb:.1f} MB)"))

        removed = self._prune(destination_dir, tag)
        if removed:
            self.stdout.write(f"Pruned {removed} old {tag} backup(s).")

        self.stdout.write(
            self.style.WARNING(
                "Reminder: a backup you have never restored is not yet a backup. "
                "Restore one into a scratch copy periodically."
            )
        )

    @staticmethod
    def _snapshot(source: Path, destination: Path) -> None:
        with sqlite3.connect(f"file:{source}?mode=ro", uri=True) as src, sqlite3.connect(
            destination
        ) as dst:
            src.backup(dst)

    @staticmethod
    def _prune(directory: Path, tag: str) -> int:
        keep = (
            settings.BACKUP_KEEP_WEEKLY if tag == "weekly" else settings.BACKUP_KEEP_DAILY
        )
        snapshots = sorted(
            directory.glob(f"mcc-{tag}-*.sqlite3"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        removed = 0
        for stale in snapshots[keep:]:
            stale.unlink(missing_ok=True)
            removed += 1
        return removed
