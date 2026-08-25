"""
Shared fixtures for the engine tests.

Mirrors the configuration `server/smoke.ts` set up in its in-memory Mongo, so the
ported checks exercise the same scenario against the same numbers. Kept
deliberately small: four task types and two members is enough to cover every
branch of the pipeline and assignment logic, and small enough to reason about
when a test fails.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta

from django.utils import timezone

from core.models import (
    Committee,
    Platform,
    PointsScheme,
    PortalSettings,
    PostSlot,
    Request,
    TaskType,
    TeamMember,
)

CAMPUS = "Permanent"
COMMITTEE_EMAIL = "marketing@iimsirmaur.ac.in"
ASHA = "asha@iimsirmaur.ac.in"
NEHA = "neha@iimsirmaur.ac.in"


def build_config() -> None:
    """Task types, slots, platforms, the point scheme and portal settings."""
    for task, skill, points, sla, at_event, requestable, internal, vertical in [
        ("Photographer", "Photography", 5, 0, True, True, True, "Photography"),
        ("Photo Editor", "Photo Editing", 3, 24, False, False, True, "Photography"),
        ("Vetter", "Vetting", 2, 24, False, False, True, ""),
        ("Event Coordinator", "Coordination", 4, 0, True, False, True, ""),
    ]:
        TaskType.objects.create(
            task=task,
            required_skill=skill,
            points=points,
            sla_hours=sla,
            at_event=at_event,
            requestable=requestable,
            internal_assignable=internal,
            vertical=vertical,
        )

    for hour in (11, 14, 17):
        PostSlot.objects.create(time=time(hour, 0))

    Platform.objects.create(platform="Instagram", handler_email=ASHA, points=2, active=True)

    settings = PortalSettings.load()
    settings.sla_hours = 48
    settings.strike_limit = 3
    settings.campus_strict = True
    settings.require_approval_always = False
    settings.strike_assignee_too = False
    settings.secretary_emails = ["poc@iimsirmaur.ac.in"]
    settings.admin_emails = ["admin@iimsirmaur.ac.in"]
    settings.allowed_domains = ["iimsirmaur.ac.in"]
    settings.default_acronym = "MEDIA"
    settings.save()

    PointsScheme.load()  # defaults already match the seeded scheme


def build_committee(**overrides) -> Committee:
    values = {
        "email": COMMITTEE_EMAIL,
        "name": "Marketing",
        "acronym": "MKTG",
        "type": "Club",
        "campus": CAMPUS,
        "last_seq": 0,
    }
    values.update(overrides)
    return Committee.objects.create(**values)


def build_team() -> tuple[TeamMember, TeamMember]:
    asha = TeamMember.objects.create(
        email=ASHA,
        name="Asha",
        campus=CAMPUS,
        year=2,
        domain_head_of="Photography",
        skills=["Photography", "Photo Editing", "Coordination"],
    )
    neha = TeamMember.objects.create(
        email=NEHA,
        name="Neha",
        campus=CAMPUS,
        year=2,
        skills=["Vetting", "Coordination"],
    )
    return asha, neha


def build_world() -> tuple[Committee, TeamMember, TeamMember]:
    """Config + committee + roster, the starting point for most tests."""
    build_config()
    committee = build_committee()
    asha, neha = build_team()
    return committee, asha, neha


def post_request(**overrides) -> Request:
    values = {
        "type": "Post",
        "event_name": "Launch Post",
        "contact_email": COMMITTEE_EMAIL,
        "platforms": ["Instagram"],
        "content_links": "http://example.invalid/asset",
        "status": "New",
    }
    values.update(overrides)
    return Request.objects.create(**values)


def coverage_request(starts_in: timedelta, duration: timedelta = timedelta(hours=2), **overrides) -> Request:
    start = timezone.now() + starts_in
    values = {
        "type": "Coverage",
        "event_name": "Fest",
        "contact_email": COMMITTEE_EMAIL,
        "venue": "Auditorium",
        "roles_needed": ["Photographer"],
        "platforms": ["Instagram"],
        "status": "New",
        "event_start": start,
        "event_end": start + duration,
    }
    values.update(overrides)
    return Request.objects.create(**values)


class FreeCalendar:
    """Everyone is always free — the stub's behaviour, stated explicitly."""

    def is_free(self, email, start, end):
        return True

    def create_hold(self, **kwargs):
        pass

    def create_reminder(self, **kwargs):
        pass


class BusyCalendar:
    """Nobody is ever free, for testing the at-event exclusion."""

    def is_free(self, email, start, end):
        return False

    def create_hold(self, **kwargs):
        pass

    def create_reminder(self, **kwargs):
        pass
