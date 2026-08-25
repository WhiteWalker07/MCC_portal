"""
Zero every team member's points.

A management command rather than a portal-admin button on purpose — this is a
rare, deliberate operation (e.g. resetting the scoreboard for a new term), not
something that should be one misclick away in the normal admin workflow. Run
it from a terminal, where doing it is a conscious act.

    python manage.py reset_points
    python manage.py reset_points --yes    # skip the confirmation prompt
"""

from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from core.activity import log_activity
from core.models import TeamMember


class Command(BaseCommand):
    help = "Reset every team member's points to 0. Strikes and everything else are untouched."

    def add_arguments(self, parser):
        parser.add_argument(
            "--yes",
            action="store_true",
            help="Skip the confirmation prompt (for non-interactive use).",
        )

    def handle(self, *args, **options):
        members = TeamMember.objects.exclude(points=0)
        count = members.count()

        if count == 0:
            self.stdout.write("Nobody has any points — nothing to do.")
            return

        if not options["yes"]:
            self.stdout.write(
                self.style.WARNING(
                    f"This will zero the points of {count} member(s). Strikes are not affected."
                )
            )
            confirmed = input("Type 'yes' to continue: ").strip().lower()
            if confirmed != "yes":
                self.stdout.write("Cancelled — nothing was changed.")
                return

        with transaction.atomic():
            members.update(points=0)
            log_activity(
                "points-reset",
                actor="admin (reset_points command)",
                detail=f"Reset points to 0 for {count} member(s).",
            )

        self.stdout.write(self.style.SUCCESS(f"Reset points to 0 for {count} member(s)."))
