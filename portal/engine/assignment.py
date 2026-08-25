"""
Manual assignment and reassignment (docs/PRD.md §5.5).

Ported from `server/src/engine/assignment.ts`: validate a specific member for a
task, and swap a task's assignee with all the bookkeeping that implies — points
moved off the old holder and onto the new one, coordinator propagation,
notifications both ways, and an audit entry.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from core.activity import log_activity
from core.constants import RequestStatus, TaskStatus
from core.models import TeamMember
from services import email as email_service
from services.calendar import calendar_service

from .assign import is_base_eligible
from .notify import award_points, notify_assignee


@dataclass(frozen=True)
class Validation:
    ok: bool
    reason: str = ""
    member: TeamMember | None = None


def validate_member(
    email: str,
    required_skill: str,
    at_event: bool,
    request_obj,
    settings,
    *,
    require_skill: bool = True,
) -> Validation:
    """
    Check one hand-picked member against the same rules auto-assignment uses.

    `require_skill=False` is for a deliberate manual reassignment across
    verticals — active/strikes/campus/calendar checks still apply, only the
    skill match is dropped.
    """
    address = (email or "").strip().lower()
    member = TeamMember.objects.filter(email=address).first()
    if member is None:
        return Validation(ok=False, reason=f"{address} is not on the team")

    if not is_base_eligible(member, required_skill, request_obj, settings, require_skill=require_skill):
        skill_clause = f', lacking "{required_skill}"' if require_skill else ""
        return Validation(
            ok=False,
            reason=(
                f"{member.name} is not eligible — inactive, out of work, at the strike "
                f"limit{skill_clause}, or on a different campus"
            ),
        )

    if at_event and request_obj.event_start and request_obj.event_end:
        free = calendar_service().is_free(address, request_obj.event_start, request_obj.event_end)
        if not free:
            return Validation(ok=False, reason=f"{member.name} is busy during the event window")

    return Validation(ok=True, member=member)


def perform_swap(task, new_member, request_obj) -> None:
    """
    Move `task` to `new_member`, applying every consequence.

    Fills an UNFILLED task just as well as it replaces an assigned one — the
    only difference is whether there are points to claw back.
    """
    old_email = (task.email or "").lower()
    _commit_swap(task, new_member, request_obj, old_email)

    notify_assignee(task)
    if old_email and old_email != new_member.email.lower():
        email_service.send(
            old_email,
            f"[Reassigned] {task.ref_code} {task.task}",
            f"Your {task.task} task on {task.ref_code} ({task.event_name}) "
            f"has been reassigned to {new_member.name}.",
        )

    log_activity(
        "reassigned",
        request_obj=request_obj,
        ref_code=task.ref_code,
        member=new_member.email,
        detail=f"{task.task}: {old_email or 'unfilled'} -> {new_member.email}",
    )


@transaction.atomic
def _commit_swap(task, new_member, request_obj, old_email: str) -> None:
    from core.constants import TASK_EVENT_COORDINATOR
    from core.models import Task

    confirmed_state = request_obj.status in RequestStatus.CONFIRMED_STATES
    points = task.points or 0
    had_points = task.points_awarded

    task.member = new_member.name
    task.email = new_member.email
    task.phone = new_member.phone or ""
    task.status = TaskStatus.CONFIRMED if confirmed_state else TaskStatus.PROPOSED
    task.points_awarded = confirmed_state
    task.save(update_fields=["member", "email", "phone", "status", "points_awarded"])

    # The outgoing holder only loses points they were actually credited.
    if had_points and old_email:
        award_points(old_email, -points)
    if confirmed_state:
        award_points(new_member.email, points)

    # Replacing the coordinator re-points the whole request at the new one, or
    # every other task on it would still escalate to the person who left.
    if task.task == TASK_EVENT_COORDINATOR:
        request_obj.coordinator_email = new_member.email
        request_obj.save(update_fields=["coordinator_email"])
        Task.objects.filter(request=request_obj).exclude(pk=task.pk).update(
            coordinator_email=new_member.email
        )
