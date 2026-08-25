"""
Engine configuration loaders.

Replaces `server/src/config.ts`. Because the old `config` collection is now real
tables, these are thin — but keeping them in one place means the engine asks for
"the task types" rather than knowing how they're stored, and there's a single
spot to add caching if the query count ever matters.
"""

from __future__ import annotations

from .models import Platform, PointsScheme, PortalSettings, PostSlot, TaskType


def get_settings() -> PortalSettings:
    return PortalSettings.load()


def get_points_scheme() -> PointsScheme:
    return PointsScheme.load()


def get_task_types() -> list[TaskType]:
    return list(TaskType.objects.all())


def get_task_type(name: str) -> TaskType | None:
    return TaskType.objects.filter(task=name).first()


def get_slots() -> list:
    """Publishing slots as `datetime.time`, earliest first."""
    return [row.time for row in PostSlot.objects.order_by("time")]


def get_platforms(active_only: bool = False) -> list[Platform]:
    queryset = Platform.objects.all()
    if active_only:
        queryset = queryset.filter(active=True)
    return list(queryset)


def get_team() -> list:
    """The full roster, as the assignment engine expects it."""
    from .models import TeamMember

    return list(TeamMember.objects.all())
