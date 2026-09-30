"""
Manual assignment and reassignment (docs/PRD.md §5.5).

Ported from `server/src/engine/assignment.ts`: validate a specific member for a
task, and swap a task's assignee with all the bookkeeping that implies —
clawing back points from the old holder if any had already been credited
(points are earned on completion, not assignment — see
engine/workflow.py's `_award_completion_points`; the new holder earns their
own on completion of the reassigned task, never a hand-me-down), coordinator
and supervisor propagation, notifications both ways, and an audit entry.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from core.activity import log_activity
from core.config import get_points_scheme
from core.constants import DERIVED_EDITOR, ROSTER_ROLES, TASK_SUPERVISOR, RequestStatus, TaskStatus
from core.models import TeamMember
from services import email as email_service
from services.calendar import calendar_service

from .assign import is_base_eligible
from .notify import award_points, notify_assignee
from .points import base_points_for


def override_proposed_assignee(task, member) -> None:
    """
    Swap a not-yet-confirmed task's assignee, no bookkeeping beyond the
    fields themselves -- unlike `perform_swap`, this runs *before* approval,
    so there's nothing to notify, claw back, or propagate yet (confirm_request
    does all of that once the request is actually approved). Used by the
    POC/Secretary's Task Supervisor pick at approval time
    (ui/views.py's approval_decide).
    """
    old_email = (task.email or "").lower()
    task.member = member.name
    task.email = member.email
    task.phone = member.phone or ""
    task.reason = ""
    fields = ["member", "email", "phone", "reason"]
    if task.status == TaskStatus.UNFILLED:
        # Filling an UNFILLED task must make it a real proposal, or
        # confirm_request skips it and the pick never takes effect.
        task.status = TaskStatus.PROPOSED
        fields.append("status")
    task.save(update_fields=fields)

    if task.task == TASK_SUPERVISOR:
        request_obj = task.request
        request_obj.supervisor_email = member.email
        request_obj.save(update_fields=["supervisor_email"])

    for editor in _editors_following(task, old_email, member):
        override_proposed_assignee(editor, member)


def _editors_following(shooter, old_email: str, new_member):
    """
    The editing tasks that should move with `shooter` to `new_member`: the ones
    paired to it that are still held by whoever held the shooter, and not done.
    An editor someone deliberately gave to a different person stays put.
    """
    if shooter.task not in DERIVED_EDITOR or (new_member.email or "").lower() == old_email:
        return []
    return [
        editor
        for editor in shooter.paired_editors.all()
        if editor.status != TaskStatus.DONE and (editor.email or "").lower() == old_email
    ]


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
    task_name: str = "",
) -> Validation:
    """
    Check one hand-picked member against the same rules auto-assignment uses.

    `require_skill=False` is for a deliberate manual reassignment across
    verticals — active/campus/year/calendar checks still apply, only the skill
    match is dropped. `task_name` selects the year rule: second-years only for
    the Task Supervisor, first-years for everything else.
    """
    address = (email or "").strip().lower()
    member = TeamMember.objects.filter(email=address).first()
    if member is None:
        return Validation(ok=False, reason=f"{address} is not on the team")

    if not is_base_eligible(
        member, required_skill, request_obj, settings, require_skill=require_skill, task_name=task_name
    ):
        if task_name == TASK_SUPERVISOR:
            year_clause = "not a second-year (only second-years supervise)"
        else:
            year_clause = "a second-year (second-years only supervise)"
        skill_clause = f', lacking "{required_skill}"' if require_skill and required_skill else ""
        return Validation(
            ok=False,
            reason=(
                f"{member.name} is not eligible — inactive, out of work, {year_clause}"
                f"{skill_clause}, or on a different campus"
            ),
        )

    if at_event and request_obj.event_start and request_obj.event_end and not request_obj.is_multiday:
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
    # Read before the swap, while the editors are still recognisably the
    # previous holder's.
    followers = _editors_following(task, old_email, new_member)
    _commit_swap(task, new_member, request_obj, old_email)

    notify_assignee(task)
    if old_email and old_email != new_member.email.lower():
        email_service.send(
            old_email,
            f"[Reassigned] {task.ref_code} {task.task}",
            f"Your {task.task} task on {task.ref_code} ({task.event_name}) "
            f"has been reassigned to {new_member.name}.",
        )

    # The club only needs to hear about this if they already know a team
    # exists (request already accepted) and the changed role is one they'd
    # actually meet on the day (ROSTER_ROLES) -- a Vetter/Graphic Designer
    # swap, or a reassignment before the club's even been told once, isn't
    # their concern.
    if task.task in ROSTER_ROLES and request_obj.status in RequestStatus.CONFIRMED_STATES:
        email_service.send(
            request_obj.contact_email,
            f"[Team update] {task.ref_code} — {request_obj.event_name}",
            f"{task.task} for {request_obj.event_name} ({task.ref_code}) is now "
            f"{new_member.name} <{new_member.email}>"
            + (f" · {new_member.phone}" if new_member.phone else "")
            + ".",
            in_reply_to=email_service.thread_id_for(task.ref_code),
        )

    log_activity(
        "reassigned",
        request_obj=request_obj,
        ref_code=task.ref_code,
        member=new_member.email,
        detail=f"{task.task}: {old_email or 'unfilled'} -> {new_member.email}",
    )

    # Each shooter edits their own work, so the editing task goes with them.
    for editor in followers:
        perform_swap(editor, new_member, request_obj)


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
    # Supervising closes itself when the Event Coordinator finishes — it has no
    # "Mark done" for a new supervisor to press, so correcting who held it after
    # the fact must leave it closed, not reopen it forever.
    # That includes a supervisor filled *after* the coordinator already finished:
    # nothing would ever close it, and it would count as load forever.
    coordinator_done = (
        task.task == TASK_SUPERVISOR
        and Task.objects.filter(
            request=request_obj, task=TASK_EVENT_COORDINATOR, status=TaskStatus.DONE
        ).exists()
    )
    stays_done = task.task == TASK_SUPERVISOR and (task.status == TaskStatus.DONE or coordinator_done)
    if stays_done:
        if task.status != TaskStatus.DONE:
            task.status = TaskStatus.DONE
            task.completed_at = timezone.now()
    else:
        task.status = TaskStatus.CONFIRMED if confirmed_state else TaskStatus.PROPOSED
    # Points are earned by whoever actually completes the task, not by being
    # handed it — the new holder starts fresh and earns their own on
    # completion (engine/workflow.py's `_award_completion_points`).
    task.points_awarded = False
    task.timing_applied = False
    if had_points:
        # `task.points` doubles as the *final*, timing-adjusted score once
        # awarded (see `_award_completion_points`) — reset it back to the
        # role's base value, or the next completion would apply a second
        # timing multiplier on top of the old one.
        task.points = base_points_for(task.task, get_points_scheme())
    # A LATE task being handed off deserves a fresh, independent deadline
    # watch for its new holder — otherwise `struck=True` permanently hides it
    # from run_deadline_check (workflow.py), which only looks at struck=False.
    task.struck = False
    task.save(
        update_fields=[
            "member",
            "email",
            "phone",
            "status",
            "points_awarded",
            "timing_applied",
            "points",
            "struck",
            "completed_at",
        ]
    )

    # The outgoing holder only loses points they were actually credited —
    # e.g. reassigning an already-completed task to correct a mistake.
    if had_points and old_email:
        award_points(old_email, -points)

    # Replacing the coordinator re-points the whole request at the new one, or
    # every other task on it would still escalate to the person who left.
    if task.task == TASK_EVENT_COORDINATOR:
        request_obj.coordinator_email = new_member.email
        request_obj.save(update_fields=["coordinator_email"])
        Task.objects.filter(request=request_obj).exclude(pk=task.pk).update(
            coordinator_email=new_member.email
        )

    # The supervisor is looked up by email on the request (permission checks,
    # the [Late] recipients), so it has to follow the task.
    if task.task == TASK_SUPERVISOR:
        request_obj.supervisor_email = new_member.email
        request_obj.save(update_fields=["supervisor_email"])
