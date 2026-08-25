"""
The workflow engine — what used to be Firestore triggers, then Express service
functions, and is now plain functions the views call directly.

    process_new_request   <- onRequestCreated
    reject_request        <- onRequestDecided (reject branch)
    complete_task         <- onTaskCompleted
    schedule_posts        <- onReadyToPost
    run_deadline_check    <- scheduledDeadlineCheck

Ported from `server/src/services/workflow.ts`. Approving a request lives in
confirm.py, since auto-acceptance and secretary approval share it.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from core.activity import log_activity
from core.config import (
    get_platforms,
    get_points_scheme,
    get_settings,
    get_slots,
    get_task_types,
    get_team,
)
from core.constants import (
    HOUR,
    RequestStatus,
    RequestType,
    TASK_EVENT_COORDINATOR,
    TASK_POST,
    TASK_VETTER,
    TaskStatus,
)
from core.models import Task, TeamMember
from services import email as email_service
from services.calendar import calendar_service

from .assign import choose_member
from .confirm import confirm_request
from .notify import add_strike, award_points
from .pipeline import build_pipeline
from .points import final_points
from .posting import find_next_slot
from .refcode import allocate_ref_code

logger = logging.getLogger(__name__)


# ── onRequestCreated ─────────────────────────────────────────────────────────


def process_new_request(request_obj) -> None:
    """
    Take a freshly submitted request from 'New' to either 'Pending for POC
    approval' or 'Request Accepted'.

    Allocates the reference code, builds and staffs the task pipeline, then
    applies the approval gate.
    """
    allocation = allocate_ref_code(request_obj)
    if not allocation.ok:
        if allocation.skipped:
            logger.info("Request %s already has a reference code; skipping", request_obj.pk)
        return

    settings = get_settings()
    scheme = get_points_scheme()
    task_types = get_task_types()
    team = get_team()
    calendar = calendar_service()

    now = timezone.now()
    pipeline = build_pipeline(request_obj, task_types, now, scheme)

    already_assigned: set[str] = set()
    coordinator_email = ""
    tasks_to_create: list[Task] = []
    outcomes: list[tuple[str, TeamMember | None, str]] = []

    for pipeline_task in pipeline:
        choice = choose_member(
            pipeline_task, request_obj, settings, team, already_assigned, calendar
        )
        member = choice.member
        if member is not None:
            already_assigned.add(member.email)
            if pipeline_task.task == TASK_EVENT_COORDINATOR:
                coordinator_email = member.email

        tasks_to_create.append(
            Task(
                request=request_obj,
                req_type=request_obj.type,
                ref_code=allocation.ref_code,
                task=pipeline_task.task,
                required_skill=pipeline_task.required_skill,
                at_event=pipeline_task.at_event,
                vertical=pipeline_task.vertical,
                member=member.name if member else "",
                email=member.email if member else "",
                phone=(member.phone or "") if member else "",
                points=pipeline_task.points,
                deadline=pipeline_task.deadline,
                status=TaskStatus.PROPOSED if member else TaskStatus.UNFILLED,
                reason="" if member else choice.reason,
                event_name=request_obj.event_name or "",
                event_start=request_obj.event_start,
                event_end=request_obj.event_end,
                venue=request_obj.venue or "",
                created_at=now,
            )
        )
        outcomes.append((pipeline_task.task, member, choice.reason))

    with transaction.atomic():
        # coordinator_email is only known after the whole pipeline is staffed,
        # so it's stamped onto every task and the request in one pass.
        for task in tasks_to_create:
            task.coordinator_email = coordinator_email
        if tasks_to_create:
            Task.objects.bulk_create(tasks_to_create)
        request_obj.coordinator_email = coordinator_email
        request_obj.save(update_fields=["coordinator_email"])

    log_activity(
        "created",
        request_obj=request_obj,
        ref_code=allocation.ref_code,
        actor=request_obj.contact_email,
        detail=f"{request_obj.type} request created",
    )
    for task_name, member, reason in outcomes:
        log_activity(
            "proposed" if member else "unfilled",
            request_obj=request_obj,
            ref_code=allocation.ref_code,
            member=member.email if member else "",
            detail=(
                f"{task_name} -> {member.name}" if member else f"{task_name} UNFILLED: {reason}"
            ),
        )

    if _requires_approval(request_obj, settings):
        request_obj.status = RequestStatus.PENDING
        request_obj.save(update_fields=["status"])
        email_service.send(
            settings.secretary_emails,
            f"[Approval needed] {allocation.ref_code} — {request_obj.event_name}",
            _approval_email(request_obj, allocation.ref_code, outcomes),
        )
        log_activity(
            "pending",
            request_obj=request_obj,
            ref_code=allocation.ref_code,
            detail="Awaiting POC approval (event within the notice window, or approval-always)",
        )
        return

    confirm_request(request_obj)


def _requires_approval(request_obj, settings) -> bool:
    """
    Short-notice Coverage requests get a human sanity check (docs/PRD.md §5.1).
    Post requests are never gated unless approval-always is on.
    """
    if settings.require_approval_always:
        return True
    if request_obj.type != RequestType.COVERAGE:
        return False
    if not request_obj.event_start:
        return False
    hours_until = (request_obj.event_start - timezone.now()).total_seconds() / HOUR
    return hours_until < settings.sla_hours


def _approval_email(request_obj, ref_code: str, outcomes) -> str:
    lines = [
        f"  {task}: {member.name} <{member.email}>" if member else f"  {task}: UNFILLED ({reason})"
        for task, member, reason in outcomes
    ]
    return (
        f"Approval needed for {ref_code} — {request_obj.event_name} "
        f"(event falls inside the approval window).\n\n"
        f"Proposed team:\n" + "\n".join(lines) + "\n\n"
        "Open the Approvals view to approve or reject.\n"
    )


# ── onRequestDecided (reject branch) ─────────────────────────────────────────


def reject_request(request_obj, reason: str, by: str) -> None:
    request_obj.status = RequestStatus.REJECTED
    request_obj.decision_by = by
    request_obj.reject_reason = reason or ""
    request_obj.decision_at = timezone.now()
    request_obj.save(update_fields=["status", "decision_by", "reject_reason", "decision_at"])

    body = f"Your request {request_obj.ref_code} ({request_obj.event_name}) was not approved."
    if reason:
        body += f"\n\nReason: {reason}"
    email_service.send(
        request_obj.contact_email,
        f"[Rejected] {request_obj.ref_code} — {request_obj.event_name}",
        body,
    )
    log_activity(
        "rejected",
        request_obj=request_obj,
        actor=by or "secretary",
        detail=reason or "Rejected by POC",
    )


# ── onTaskCompleted ──────────────────────────────────────────────────────────


def complete_task(task) -> None:
    """
    Apply the consequences of a task reaching DONE: the completion-timing points
    modifier, then advancing the parent request if this was the last thing it
    was waiting on.

    The caller has already set the task to DONE and stamped `completed_at`.
    """
    if task.status != TaskStatus.DONE:
        return

    request_obj = task.request

    log_activity(
        "completed",
        request_obj=request_obj,
        ref_code=task.ref_code,
        member=task.email,
        detail=f"{task.task} marked done",
    )

    _apply_timing_modifier(task, request_obj)

    # Only ever advance from 'Request Accepted' — a request that's already
    # covered, ready or posted has moved past this point.
    if request_obj.status != RequestStatus.ACCEPTED:
        return

    tasks = list(request_obj.tasks.all())

    if task.req_type == RequestType.COVERAGE:
        deliverables = [
            t for t in tasks if t.task != TASK_EVENT_COORDINATOR and t.status != TaskStatus.UNFILLED
        ]
        if deliverables and all(t.status == TaskStatus.DONE for t in deliverables):
            request_obj.status = RequestStatus.EVENT_COVERED
            request_obj.save(update_fields=["status"])
            log_activity(
                "event-covered",
                request_obj=request_obj,
                ref_code=task.ref_code,
                actor="engine",
                detail="All coverage deliverables done",
            )
        return

    # A Post request needs only its Vetter; finishing that sends it to scheduling.
    vetter = next((t for t in tasks if t.task == TASK_VETTER), None)
    if vetter is not None and vetter.status == TaskStatus.DONE:
        request_obj.status = RequestStatus.READY_TO_POST
        request_obj.save(update_fields=["status"])
        log_activity(
            "ready",
            request_obj=request_obj,
            ref_code=task.ref_code,
            actor="engine",
            detail="Vetting done; ready to post",
        )
        schedule_posts(request_obj)


def _apply_timing_modifier(task, request_obj) -> None:
    """
    Adjust a completed task's points for how promptly it was delivered
    (docs/PRD.md §5.7).

    Guarded four ways, all of which matter: the coordinator role isn't a
    deliverable, points must already have been awarded to adjust, `timing_applied`
    makes it once-only, and an unassigned task has nobody to credit.
    """
    if task.task == TASK_EVENT_COORDINATOR:
        return
    if not task.points_awarded or task.timing_applied or not task.email:
        return

    reference = task.event_end if task.req_type == RequestType.COVERAGE else task.created_at
    if reference is None:
        # Nothing to measure against; mark it handled so we don't retry forever.
        task.timing_applied = True
        task.save(update_fields=["timing_applied"])
        return

    completed_at = task.completed_at or timezone.now()
    turnaround_hours = (completed_at - reference).total_seconds() / HOUR

    scheme = get_points_scheme()
    base = task.points or 0
    adjusted = final_points(base, turnaround_hours, scheme)
    delta = adjusted - base

    task.points = adjusted
    task.timing_applied = True
    task.save(update_fields=["points", "timing_applied"])

    if delta:
        award_points(task.email, delta)
        log_activity(
            "points-adjust",
            request_obj=request_obj,
            ref_code=task.ref_code,
            member=task.email,
            detail=f"{task.task}: {delta:+d} pts (turnaround {round(turnaround_hours)}h)",
        )


# ── onReadyToPost ────────────────────────────────────────────────────────────


def schedule_posts(request_obj) -> None:
    """
    Schedule one task per requested platform into the next free slot
    (docs/PRD.md §5.6).

    Idempotent: if Post tasks already exist for this request, it does nothing —
    so a retry can't double-book a handler or double-award points.
    """
    if request_obj.status != RequestStatus.READY_TO_POST:
        return
    if request_obj.tasks.filter(task=TASK_POST).exists():
        return

    ref_code = request_obj.ref_code or ""
    platforms = list(dict.fromkeys(p for p in (request_obj.platforms or []) if p))

    if not platforms:
        request_obj.status = RequestStatus.POSTED
        request_obj.posts = []
        request_obj.save(update_fields=["status", "posts"])
        log_activity(
            "posted",
            request_obj=request_obj,
            ref_code=ref_code,
            actor="engine",
            detail="No platforms selected",
        )
        return

    platform_rows = get_platforms()
    slots = get_slots()
    now = timezone.now()

    # Existing bookings, so we neither reuse a slot nor pile everything onto one
    # handler when a platform has several.
    taken_by_platform: dict[str, set] = {}
    load_by_email: dict[str, int] = {}
    for existing in Task.objects.filter(status=TaskStatus.SCHEDULED, task=TASK_POST):
        if existing.scheduled_at:
            taken_by_platform.setdefault(existing.platform, set()).add(existing.scheduled_at)
        address = (existing.email or "").lower()
        if address:
            load_by_email[address] = load_by_email.get(address, 0) + 1

    new_tasks: list[Task] = []
    posts: list[dict] = []

    for platform in platforms:
        handlers = [r for r in platform_rows if r.platform == platform and r.active]

        if not handlers:
            new_tasks.append(
                _post_task(request_obj, platform, "", 0, None, TaskStatus.UNFILLED, "no active handler")
            )
            posts.append(
                {"platform": platform, "handlerEmail": "", "scheduledAt": None, "status": TaskStatus.UNFILLED}
            )
            continue

        handler = min(handlers, key=lambda h: load_by_email.get((h.handler_email or "").lower(), 0))

        taken = taken_by_platform.setdefault(platform, set())
        slot = find_next_slot(slots, taken, now)
        taken.add(slot)
        handler_key = (handler.handler_email or "").lower()
        load_by_email[handler_key] = load_by_email.get(handler_key, 0) + 1

        new_tasks.append(
            _post_task(
                request_obj,
                platform,
                handler.handler_email,
                handler.points or 0,
                slot,
                TaskStatus.SCHEDULED,
                "",
            )
        )
        posts.append(
            {
                "platform": platform,
                "handlerEmail": handler.handler_email,
                "scheduledAt": slot.isoformat(),
                "status": TaskStatus.SCHEDULED,
            }
        )

    with transaction.atomic():
        Task.objects.bulk_create(new_tasks)

        # Credit post points, but only to handlers who are actually on the team —
        # a shared channel inbox isn't a person and has no score.
        scheduled_emails = {
            (p["handlerEmail"] or "").lower()
            for p in posts
            if p["status"] == TaskStatus.SCHEDULED and p["handlerEmail"]
        }
        team_handlers = set(
            TeamMember.objects.filter(email__in=scheduled_emails).values_list("email", flat=True)
        )
        for post in posts:
            if post["status"] != TaskStatus.SCHEDULED:
                continue
            address = (post["handlerEmail"] or "").lower()
            if address not in team_handlers:
                continue
            row = next(
                (
                    r
                    for r in platform_rows
                    if r.platform == post["platform"] and r.handler_email == post["handlerEmail"]
                ),
                None,
            )
            if row and row.points:
                award_points(address, row.points)

        request_obj.status = RequestStatus.POSTED
        request_obj.posts = posts
        request_obj.save(update_fields=["status", "posts"])

    calendar = calendar_service()
    for task in new_tasks:
        if task.status != TaskStatus.SCHEDULED or not task.scheduled_at:
            continue
        calendar.create_hold(
            email=task.email,
            title=f"{ref_code} Post ({task.platform}) — {task.event_name}",
            start=task.scheduled_at,
            end=task.scheduled_at,
            description=task.event_name,
        )
        email_service.send(
            task.email,
            f"[Scheduled] {ref_code} {task.platform} post — {task.event_name}",
            f"A {task.platform} post for {task.event_name} ({ref_code}) is scheduled for "
            f"{task.scheduled_at:%d %b %Y, %H:%M}.",
        )

    log_activity(
        "posted",
        request_obj=request_obj,
        ref_code=ref_code,
        actor="engine",
        detail=f"{sum(1 for p in posts if p['status'] == TaskStatus.SCHEDULED)} post(s) scheduled",
    )


def _post_task(request_obj, platform, email, points, scheduled_at, status, reason) -> Task:
    return Task(
        request=request_obj,
        req_type=request_obj.type,
        ref_code=request_obj.ref_code or "",
        task=TASK_POST,
        required_skill="",
        at_event=False,
        vertical="",
        platform=platform,
        member=email or "",
        email=email or "",
        phone="",
        points=points,
        deadline=scheduled_at,
        scheduled_at=scheduled_at,
        status=status,
        reason=reason or "",
        coordinator_email=request_obj.coordinator_email or "",
        event_name=request_obj.event_name or "",
        event_start=request_obj.event_start,
        event_end=request_obj.event_end,
        venue=request_obj.venue or "",
        created_at=timezone.now(),
    )


# ── scheduledDeadlineCheck ───────────────────────────────────────────────────


def run_deadline_check() -> dict:
    """
    Find overdue confirmed tasks, mark them LATE and issue strikes
    (docs/PRD.md §5.8).

    Driven hourly by Windows Task Scheduler via `manage.py deadline_check`.
    Returns a summary so the command can report it.
    """
    settings = get_settings()
    now = timezone.now()

    overdue = list(
        Task.objects.filter(status=TaskStatus.CONFIRMED, deadline__lt=now, struck=False)
        .exclude(task=TASK_EVENT_COORDINATOR)  # the coordinator isn't a deliverable
        .select_related("request")
    )

    if not overdue:
        logger.info("deadline check: nothing overdue")
        return {"late": 0, "checked_at": now}

    late_count = 0
    for task in overdue:
        coordinator = (task.coordinator_email or "").lower()
        assignee = (task.email or "").lower()
        try:
            with transaction.atomic():
                task.status = TaskStatus.LATE
                task.struck = True
                task.save(update_fields=["status", "struck"])
                if coordinator:
                    add_strike(coordinator)
                if settings.strike_assignee_too and assignee:
                    add_strike(assignee)
            late_count += 1
        except Exception as exc:
            logger.error("deadline check failed for task %s: %s", task.pk, exc)
            continue

        recipients = [
            address
            for address in (
                coordinator,
                assignee if settings.strike_assignee_too else "",
                settings.head_email or "",
            )
            if address
        ]
        email_service.send(
            recipients,
            f"[Late] {task.ref_code} {task.task}",
            f"{task.task} on {task.ref_code} ({task.event_name}) is past its deadline "
            f"and is now marked LATE.",
        )
        log_activity(
            "late",
            request_obj=task.request,
            ref_code=task.ref_code,
            member=assignee,
            detail=f"{task.task} marked LATE",
        )
        if coordinator:
            log_activity(
                "strike",
                request_obj=task.request,
                ref_code=task.ref_code,
                member=coordinator,
                detail=f"Strike to coordinator for late {task.task}",
            )

    logger.info("deadline check: marked %d task(s) LATE", late_count)
    return {"late": late_count, "checked_at": now}
