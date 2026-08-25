"""
Load the real committees, media-team roster and engine configuration.

Replaces `npm run load-data` (server/scripts/load-real-data.mjs). Safe to re-run
at any time — that is the whole point. Engine-owned fields are only ever set on
insert, so re-seeding after a roster edit never resets anyone's points, strikes,
availability history or reference-code counters.

    python manage.py seed_real_data
    python manage.py seed_real_data --reset-config   # also restore tuned config
"""

from __future__ import annotations

from datetime import datetime

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from core import seed_data
from core.models import (
    Committee,
    Platform,
    PointsScheme,
    PortalSettings,
    PostSlot,
    TaskType,
    TeamMember,
)


class Command(BaseCommand):
    help = "Load the real committees, team roster and engine configuration (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--reset-config",
            action="store_true",
            help=(
                "Also overwrite task types, slots, platforms and the point scheme "
                "with the values in core/seed_data.py. Without this they are only "
                "created when missing, so admin-tuned values survive a re-seed."
            ),
        )

    @transaction.atomic
    def handle(self, *args, **options):
        reset_config = options["reset_config"]

        committees = self._load_committees()
        members = self._load_team()
        self._load_task_types(reset_config)
        self._load_slots(reset_config)
        self._load_platforms(reset_config)
        self._load_points(reset_config)
        self._load_settings()

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded {committees['created']} new / {committees['updated']} updated committees, "
                f"{members['created']} new / {members['updated']} updated team members."
            )
        )
        if not reset_config:
            self.stdout.write(
                "Engine config left as-is where it already existed "
                "(pass --reset-config to restore the defaults)."
            )

    # ── committees ───────────────────────────────────────────────────────────

    def _load_committees(self) -> dict:
        created = updated = 0
        for email, name, acronym, type_, campus in seed_data.COMMITTEES:
            _, was_created = Committee.objects.update_or_create(
                email=email.lower(),
                defaults={"name": name, "acronym": acronym, "type": type_, "campus": campus},
                # last_seq and logo are engine/admin owned — never reset them.
                create_defaults={
                    "name": name,
                    "acronym": acronym,
                    "type": type_,
                    "campus": campus,
                    "last_seq": 0,
                    "logo": "",
                },
            )
            created += was_created
            updated += not was_created
        return {"created": created, "updated": updated}

    # ── team ─────────────────────────────────────────────────────────────────

    def _load_team(self) -> dict:
        created = updated = 0
        now = timezone.now()
        for email, name, vertical, campus, year, skills in seed_data.TEAM:
            shared = {
                "name": name,
                "vertical": vertical,
                "campus": campus,
                "year": year,
                "skills": seed_data.team_skills(year, skills),
                "active": True,
            }
            _, was_created = TeamMember.objects.update_or_create(
                email=email.lower(),
                defaults=shared,
                # Scores, strikes, headship and availability history belong to
                # the engine and to decisions made in the portal — a re-seed
                # must never wipe them.
                create_defaults={
                    **shared,
                    "phone": "",
                    "points": 0,
                    "strikes": 0,
                    "domain_head_of": "",
                    "availability": "available",
                    "availability_changed_at": now,
                    "on_work_days": 0.0,
                    "out_days": 0.0,
                },
            )
            created += was_created
            updated += not was_created
        return {"created": created, "updated": updated}

    # ── engine configuration ─────────────────────────────────────────────────

    def _load_task_types(self, reset: bool) -> None:
        for (
            task,
            skill,
            points,
            sla_hours,
            at_event,
            requestable,
            internal,
            vertical,
        ) in seed_data.TASK_TYPES:
            values = {
                "required_skill": skill,
                "points": points,
                "sla_hours": sla_hours,
                "at_event": at_event,
                "requestable": requestable,
                "internal_assignable": internal,
                "vertical": vertical,
            }
            if reset:
                TaskType.objects.update_or_create(task=task, defaults=values)
            else:
                TaskType.objects.get_or_create(task=task, defaults=values)

    def _load_slots(self, reset: bool) -> None:
        if reset:
            PostSlot.objects.all().delete()
        for raw in seed_data.SLOTS:
            PostSlot.objects.get_or_create(time=datetime.strptime(raw, "%H:%M").time())

    def _load_platforms(self, reset: bool) -> None:
        for platform, handler, points, active in seed_data.PLATFORMS:
            values = {"points": points, "active": active}
            if reset:
                Platform.objects.update_or_create(
                    platform=platform, handler_email=handler, defaults=values
                )
            else:
                Platform.objects.get_or_create(
                    platform=platform, handler_email=handler, defaults=values
                )

    def _load_points(self, reset: bool) -> None:
        scheme = PointsScheme.load()
        # Only stamp the defaults onto a scheme nobody has tuned yet, unless the
        # caller explicitly asked to restore them.
        if reset or not PointsScheme.objects.filter(pk=1).exists():
            for field, value in seed_data.POINTS_SCHEME.items():
                setattr(scheme, field, value)
            scheme.save()

    def _load_settings(self) -> None:
        """
        Settings are always refreshed, because this is how admin and secretary
        rights are granted — running the seed is the documented way to (re)apply
        them (docs/PIC.md §1). general_seq is left alone; it's a live counter.
        """
        settings_row = PortalSettings.load()
        for field, value in seed_data.SETTINGS.items():
            setattr(settings_row, field, value)
        settings_row.admin_emails = list(seed_data.ADMIN_EMAILS)
        settings_row.secretary_emails = list(seed_data.SECRETARY_EMAILS)
        settings_row.allowed_domains = list(seed_data.ALLOWED_DOMAINS)
        settings_row.save()
