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
from core.constants import (
    DERIVED_EDITOR,
    ROSTER_ROLES,
    TASK_EVENT_COORDINATOR,
    TASK_SUPERVISOR,
    RequestStatus,
    RequestType,
    TaskStatus,
)
from core.models import TeamMember
from services import email as email_service
from services.calendar import calendar_service

from .assign import is_base_eligible
from .notify import award_points, notify_assignee
from .points import base_points_for


class RemovalError(ValueError):
    """A removal the rules don't allow; the message is shown to the user."""


def remove_task(task, actor: str) -> list[str]:
    """
    Take a task off a Coverage event. Taking off a photographer or videographer also
    takes the editing that goes with them (each shooter edits their own work, so it
    has nothing to attach to once they are gone). Any other task, including an
    editing task on its own, goes alone. The Event Coordinator and the Task
    Supervisor can't be removed (reassign them). Only a task that isn't done yet, on
    a request that isn't closed. Returns the labels of what was removed.

    Anyone who had been told about it (the task was confirmed) gets one email
    listing what was taken off them; if the club had been given their contact, it is
    told the team changed. The Event Coordinator's deadline is recalculated, since
    one of the tasks it is measured from has gone.
    """
    from core.models import Task

    from .pipeline import refresh_coordinator_deadline
    from .workflow import advance_if_covered  # local: workflow imports this module's neighbours

    request_obj = task.request
    if task.task in (TASK_EVENT_COORDINATOR, TASK_SUPERVISOR):
        raise RemovalError(f"The {task.task} can't be removed: reassign it to someone else instead.")
    if request_obj.type != RequestType.COVERAGE:
        raise RemovalError("Tasks can only be removed from a Coverage request.")
    if request_obj.status in RequestStatus.TERMINAL:
        raise RemovalError("That request is closed: its team can no longer be changed.")

    with transaction.atomic():
        locked = Task.objects.select_for_update().select_related("sub_event").get(pk=task.pk)
        if locked.status == TaskStatus.DONE:
            raise RemovalError("That task is already done, so it can't be removed.")
        editors = [e for e in locked.paired_editors.select_for_update() if e.status != TaskStatus.DONE]
        doomed = [locked, *editors]
        snapshot = [(t.label, t.email, t.member, t.status, t.task in ROSTER_ROLES) for t in doomed]
        for t in doomed:
            t.delete()

        accepted = request_obj.status in RequestStatus.CONFIRMED_STATES
        if accepted and request_obj.roster:
            roster = list(request_obj.roster)
            for label, email, _, _, in_roster in snapshot:
                if in_roster:
                    entry = next((e for e in roster if e.get("role") == label and e.get("email") == email), None)
                    if entry is not None:
                        roster.remove(entry)
            request_obj.roster = roster
            request_obj.save(update_fields=["roster"])
        refresh_coordinator_deadline(request_obj)

    ref = request_obj.ref_code
    removed_labels = [label for label, *_ in snapshot]

    # One email per person who had been told, listing everything taken off them.
    told: dict[str, list[str]] = {}
    for label, email, _, status, _ in snapshot:
        if email and status in (TaskStatus.CONFIRMED, TaskStatus.LATE):
            told.setdefault(email, []).append(label)
    for email, labels in told.items():
        email_service.send(
            email,
            f"[Removed] {ref} {labels[0]} — {request_obj.event_name}",
            f"You are no longer assigned to {request_obj.event_name} ({ref}):\n"
            + "\n".join(f"  {label}" for label in labels)
            + "\n\nIt was removed by the event's coordinator or the POC. If you think this is a mistake, "
            "ask them.\n",
        )

    shooter_label, shooter_email, shooter_name, shooter_status, in_roster = snapshot[0]
    if accepted and in_roster and shooter_email:
        email_service.send(
            request_obj.contact_email,
            f"[Team update] {ref} — {request_obj.event_name}",
            f"{shooter_name} <{shooter_email}> is no longer on the team for {request_obj.event_name} "
            f"({ref}): the {shooter_label} role has been taken off it. If someone is added in their "
            "place, you will be sent their details.",
            in_reply_to=email_service.thread_id_for(ref),
        )
    # The task taken off may have been the last deliverable the event was waiting for.
    advance_if_covered(request_obj)
    log_activity(
        "task-removed",
        request_obj=request_obj,
        ref_code=ref,
        actor=actor,
        member=shooter_email,
        detail="Removed " + ", ".join(removed_labels),
    )
    return removed_labels


class CoordinatorError(ValueError):
    """An additional-coordinator change the rules don't allow; the message is shown to the user."""


def add_additional_coordinator(request_obj, email: str, actor: str):
    """
    Give a Coverage request a second Event Coordinator, with all the main
    coordinator's powers (they appear in Assignments, can reassign, add and remove
    tasks, mark ready, and hand the coverage to the club). The POC/Admin's call; at
    most one extra, a different person from the main coordinator, and checked by the
    usual rules (an active first-year, not Out of work, on the club's campus).

    Once the request is accepted they are confirmed, emailed and added to the club's
    team list (the club is told); before that they are proposed and the approval
    confirms them. They are scored on their own task like the main coordinator.
    """
    from core.config import get_settings
    from core.models import Task, TaskType

    from .pipeline import refresh_coordinator_deadline
    from .workflow import add_task_from_type

    if request_obj.type != RequestType.COVERAGE:
        raise CoordinatorError("Only a Coverage request has an Event Coordinator.")
    if request_obj.status in RequestStatus.TERMINAL:
        raise CoordinatorError("That request is closed: its team can no longer be changed.")
    if request_obj.co_coordinator_email or request_obj.tasks.filter(task=TASK_EVENT_COORDINATOR, additional=True).exists():
        raise CoordinatorError("This request already has an additional coordinator: replace or remove them instead.")
    address = (email or "").strip().lower()
    if not address:
        raise CoordinatorError("Pick who the additional coordinator is.")
    if address in request_obj.coordinator_emails:
        raise CoordinatorError("That person is already this event's coordinator.")
    ec_type = TaskType.objects.filter(task=TASK_EVENT_COORDINATOR).first()
    if ec_type is None:
        raise CoordinatorError("The Event Coordinator task type isn't set up.")

    verdict = validate_member(
        address, ec_type.required_skill, ec_type.at_event, request_obj, get_settings(),
        require_skill=False, task_name=TASK_EVENT_COORDINATOR,
    )
    if not verdict.ok:
        raise CoordinatorError(verdict.reason)
    member = verdict.member

    accepted = request_obj.status in RequestStatus.CONFIRMED_STATES
    with transaction.atomic():
        task = add_task_from_type(
            request_obj, ec_type, member,
            status=TaskStatus.CONFIRMED if accepted else TaskStatus.PROPOSED,
            coordinator_email=request_obj.coordinator_email, additional=True,
        )
        request_obj.co_coordinator_email = member.email
        update_fields = ["co_coordinator_email"]
        if accepted:
            request_obj.roster = [
                *(request_obj.roster or []),
                {"role": task.label, "name": member.name, "email": member.email, "phone": member.phone or ""},
            ]
            update_fields.append("roster")
        request_obj.save(update_fields=update_fields)
        # Both coordinators are due at the same time: 12 hours after the last other task.
        refresh_coordinator_deadline(request_obj)
        task.refresh_from_db()

    if accepted:
        notify_assignee(task)
        email_service.send(
            request_obj.contact_email,
            f"[Team update] {request_obj.ref_code} — {request_obj.event_name}",
            f"{member.name} <{member.email}>"
            + (f" · {member.phone}" if member.phone else "")
            + f" is now also coordinating {request_obj.event_name} ({request_obj.ref_code}), "
            "alongside the existing coordinator.",
            in_reply_to=email_service.thread_id_for(request_obj.ref_code),
        )
    log_activity(
        "coordinator-added",
        request_obj=request_obj,
        ref_code=request_obj.ref_code,
        actor=actor,
        member=member.email,
        detail=f"Additional Event Coordinator: {member.name}",
    )
    return task


def remove_additional_coordinator(request_obj, actor: str) -> str:
    """
    Take the additional coordinator off a request, if their task isn't already
    closed. Returns their name. The main coordinator is unaffected (to replace the
    main one, reassign them).
    """
    from core.models import Task

    with transaction.atomic():
        task = (
            Task.objects.select_for_update()
            .filter(request=request_obj, task=TASK_EVENT_COORDINATOR, additional=True)
            .first()
        )
        if task is None:
            raise CoordinatorError("This request has no additional coordinator.")
        if request_obj.status in RequestStatus.TERMINAL:
            raise CoordinatorError("That request is closed: its team can no longer be changed.")
        if task.status == TaskStatus.DONE:
            raise CoordinatorError("Their task is already closed, so they can't be removed.")
        name, email, status = task.member, task.email, task.status
        label = task.label
        task.delete()
        request_obj.co_coordinator_email = ""
        update_fields = ["co_coordinator_email"]
        if request_obj.roster:
            roster = list(request_obj.roster)
            entry = next((e for e in roster if e.get("role") == label and e.get("email") == email), None)
            if entry is not None:
                roster.remove(entry)
                request_obj.roster = roster
                update_fields.append("roster")
        request_obj.save(update_fields=update_fields)

    ref = request_obj.ref_code
    if email and status in (TaskStatus.CONFIRMED, TaskStatus.LATE):
        email_service.send(
            email,
            f"[Removed] {ref} Event Coordinator — {request_obj.event_name}",
            f"You are no longer an additional Event Coordinator for {request_obj.event_name} ({ref}). "
            "If you think this is a mistake, ask the POC.\n",
        )
    if request_obj.status in RequestStatus.CONFIRMED_STATES and email:
        email_service.send(
            request_obj.contact_email,
            f"[Team update] {ref} — {request_obj.event_name}",
            f"{name} <{email}> is no longer also coordinating {request_obj.event_name} ({ref}).",
            in_reply_to=email_service.thread_id_for(ref),
        )
    log_activity(
        "coordinator-removed", request_obj=request_obj, ref_code=ref, actor=actor, member=email,
        detail=f"Additional Event Coordinator removed: {name}",
    )
    return name


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
    window: tuple | None = None,
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

    # `window` is a sub-event's own time; absent, the whole event's (a multi-day
    # event has no single window, so its whole-event tasks aren't checked).
    if window is None and request_obj.event_start and request_obj.event_end and not request_obj.is_multiday:
        window = (request_obj.event_start, request_obj.event_end)
    if at_event and window and window[0] and window[1]:
        free = calendar_service().is_free(address, window[0], window[1])
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
            f"[Reassigned] {task.ref_code} {task.label}",
            f"Your {task.label} task on {task.ref_code} ({task.event_name}) "
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
            f"{task.label} for {request_obj.event_name} ({task.ref_code}) is now "
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
        detail=f"{task.label}: {old_email or 'unfilled'} -> {new_member.email}",
    )

    # Each shooter edits their own work, so the editing task goes with them.
    for editor in followers:
        perform_swap(editor, new_member, request_obj)


@transaction.atomic
def _commit_swap(task, new_member, request_obj, old_email: str) -> None:
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
    if task.task == TASK_EVENT_COORDINATOR and task.additional:
        # The additional coordinator is a person of their own: replacing them only
        # changes who that is. The main coordinator, and what other tasks escalate
        # to, stay as they were.
        request_obj.co_coordinator_email = new_member.email
        request_obj.save(update_fields=["co_coordinator_email"])
    elif task.task == TASK_EVENT_COORDINATOR:
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
