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
    TASK_CONTENT_WRITER,
    TASK_EVENT_COORDINATOR,
    TASK_GRAPHIC_DESIGNER,
    TASK_POST,
    TASK_SUPERVISOR,
    TASK_VETTER,
    TaskStatus,
)
from core.models import Task, TeamMember
from services import email as email_service
from services.calendar import calendar_service

from .assign import choose_member, choose_supervisor
from .confirm import confirm_request
from .notify import award_points
from .pipeline import build_pipeline
from .points import final_points
from .posting import find_next_slot
from .refcode import allocate_ref_code

logger = logging.getLogger(__name__)


# ── onRequestCreated ─────────────────────────────────────────────────────────


def open_supervision_counts() -> dict[str, int]:
    """
    How many unfinished supervisions each second-year currently holds, keyed by
    lower-cased email — what `choose_supervisor` balances on.

    Requests that are already Posted or Rejected are ignored: a rejected
    request keeps its PROPOSED supervisor task forever, and counting it would
    quietly make someone look busier than they are.
    """
    open_tasks = (
        Task.objects.filter(task=TASK_SUPERVISOR)
        .exclude(status__in=[TaskStatus.DONE, TaskStatus.UNFILLED])
        .exclude(request__status__in=RequestStatus.TERMINAL)
        .values_list("email", flat=True)
    )
    counts: dict[str, int] = {}
    for address in open_tasks:
        key = (address or "").lower()
        if key:
            counts[key] = counts.get(key, 0) + 1
    return counts


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
    supervisor_email = ""
    tasks_to_create: list[Task] = []
    outcomes: list[tuple[str, TeamMember | None, str]] = []
    open_counts = open_supervision_counts()

    for pipeline_task in pipeline:
        if pipeline_task.task == TASK_SUPERVISOR:
            choice = choose_supervisor(request_obj, settings, team, open_counts)
        else:
            choice = choose_member(
                pipeline_task, request_obj, settings, team, already_assigned, calendar
            )
        member = choice.member
        if member is not None:
            if pipeline_task.task == TASK_SUPERVISOR:
                supervisor_email = member.email
            else:
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
        request_obj.supervisor_email = supervisor_email
        request_obj.save(update_fields=["coordinator_email", "supervisor_email"])

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

    Post requests always get one: the POC/Secretary does a background check on
    the submitted content before any team is confirmed.
    """
    if settings.require_approval_always:
        return True
    if request_obj.type == RequestType.POST:
        return True
    if not request_obj.event_start:
        return False
    hours_until = (request_obj.event_start - timezone.now()).total_seconds() / HOUR
    return hours_until < settings.sla_hours


def _approval_email(request_obj, ref_code: str, outcomes) -> str:
    lines = [
        f"  {task}: {member.name} <{member.email}>" if member else f"  {task}: UNFILLED ({reason})"
        for task, member, reason in outcomes
    ]
    reason = (
        "event falls inside the approval window"
        if request_obj.type == RequestType.COVERAGE
        else "Post requests always need a review before the team is confirmed"
    )
    schedule = ""
    sub_events = list(request_obj.sub_events.all()) if request_obj.pk else []
    if sub_events:
        schedule = "Sub-events:\n" + "\n".join(
            f"  {s.name}: {timezone.localtime(s.start):%d %b, %H:%M}–{timezone.localtime(s.end):%H:%M}"
            + (f" · {s.venue}" if s.venue else "")
            for s in sub_events
        ) + "\n\n"
    return (
        f"Approval needed for {ref_code} — {request_obj.event_name} ({reason}).\n\n"
        f"{schedule}"
        f"Proposed team:\n" + "\n".join(lines) + "\n\n"
        "Open the Approvals view to approve or reject.\n"
    )


# ── onRequestDecided (reject branch) ─────────────────────────────────────────


def reject_request(request_obj, reason: str, by: str) -> bool:
    """
    Reject a request. Returns False, having changed nothing, if someone else got
    there first: an approval racing this rejection may already have accepted it
    (tasks confirmed, people told), and a second rejection must not email the
    club twice.
    """
    with transaction.atomic():
        locked_status = (
            type(request_obj)
            .objects.select_for_update()
            .values_list("status", flat=True)
            .get(pk=request_obj.pk)
        )
        if locked_status == RequestStatus.REJECTED or locked_status in RequestStatus.CONFIRMED_STATES:
            return False
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
    return True


# ── onTaskCompleted ──────────────────────────────────────────────────────────


def complete_task(task) -> None:
    """
    Apply the consequences of a task reaching DONE: credit its points, then
    advance the parent request if this was the last thing it was waiting on.

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

    _award_completion_points(task, request_obj)

    # The coordinator's own completion is the coverage hand-off to the
    # requesting club -- ui/views.py's task_complete required a drive link
    # before allowing this, so it's on the request by now. Unconditional on
    # request status: it's about notifying the club, not advancing the
    # pipeline (that still only happens from 'Request Accepted', below).
    if task.task == TASK_EVENT_COORDINATOR and task.req_type == RequestType.COVERAGE:
        _notify_club_coverage_shared(task, request_obj)
        # The Task Supervisor has no "Mark done" of its own: it closes here.
        _close_supervision(request_obj)

    # Only ever advance from 'Request Accepted' — a request that's already
    # covered, ready or posted has moved past this point.
    if request_obj.status != RequestStatus.ACCEPTED:
        return

    tasks = list(request_obj.tasks.all())

    if task.req_type == RequestType.COVERAGE:
        # Neither the coordinator nor the supervisor is a deliverable.
        deliverables = [
            t
            for t in tasks
            if t.task not in (TASK_EVENT_COORDINATOR, TASK_SUPERVISOR)
            and t.status != TaskStatus.UNFILLED
        ]
        if deliverables and all(t.status == TaskStatus.DONE for t in deliverables):
            # Claimed atomically: the last two deliverables finishing at once
            # would otherwise both advance it (and both log it).
            if not _advance(request_obj, RequestStatus.ACCEPTED, RequestStatus.EVENT_COVERED):
                return
            log_activity(
                "event-covered",
                request_obj=request_obj,
                ref_code=task.ref_code,
                actor="engine",
                detail="All coverage deliverables done",
            )
        return

    # A Post is ready once its makers are done.
    #
    # A request accepted before the Vetter was dropped still has one, and keeps
    # the old rule (its Vetter finishing sends it to scheduling) so nothing in
    # flight gets stuck. Every newer Post has no vetting step: it goes to
    # scheduling automatically once every staffed Graphic Designer and Content
    # Writer task is done.
    vetter = next((t for t in tasks if t.task == TASK_VETTER), None)
    if vetter is not None:
        ready = vetter.status == TaskStatus.DONE
        detail = "Vetting done; ready to post"
    else:
        # An UNFILLED maker still blocks: the graphic heads are told to fill it,
        # and posting without it would schedule a post that has no graphic (or
        # caption) while the late-filled role could no longer change anything.
        makers = [t for t in tasks if t.task in (TASK_CONTENT_WRITER, TASK_GRAPHIC_DESIGNER)]
        ready = bool(makers) and all(t.status == TaskStatus.DONE for t in makers)
        detail = "Graphic Designer and Content Writer done; ready to post"

    if ready:
        # Claimed atomically: the last two makers finishing at once would
        # otherwise both run schedule_posts, whose own exists() guard is an
        # unlocked read — duplicate Post tasks and handler points credited twice.
        if not _advance(request_obj, RequestStatus.ACCEPTED, RequestStatus.READY_TO_POST):
            return
        log_activity(
            "ready",
            request_obj=request_obj,
            ref_code=task.ref_code,
            actor="engine",
            detail=detail,
        )
        schedule_posts(request_obj)


def _advance(request_obj, from_status: str, to_status: str) -> bool:
    """Move the request from one status to the next, only if it's still in the first."""
    moved = type(request_obj).objects.filter(pk=request_obj.pk, status=from_status).update(
        status=to_status
    )
    if moved:
        request_obj.status = to_status
    return bool(moved)


def _close_supervision(request_obj) -> None:
    """
    Mark the request's Task Supervisor task DONE, no points. It rides on the
    Event Coordinator's completion because supervising has no deliverable of
    its own to hand in.
    """
    now = timezone.now()
    for supervision in request_obj.tasks.filter(task=TASK_SUPERVISOR).exclude(
        status__in=[TaskStatus.DONE, TaskStatus.UNFILLED]
    ):
        supervision.status = TaskStatus.DONE
        supervision.completed_at = now
        supervision.save(update_fields=["status", "completed_at"])
        log_activity(
            "supervision-closed",
            request_obj=request_obj,
            ref_code=supervision.ref_code,
            member=supervision.email,
            actor="engine",
            detail="Task Supervisor closed: the Event Coordinator marked the event done",
        )


def _notify_club_coverage_shared(task, request_obj) -> None:
    """
    Tell the requesting club coverage is done and where to find it, right
    when the Event Coordinator attaches the drive link and marks their own
    task complete (docs/PRD.md §5 — the coordinator hand-off).
    """
    email_service.send(
        request_obj.contact_email,
        f"[Covered] {task.ref_code} — {request_obj.event_name}",
        f"Coverage for {request_obj.event_name} ({task.ref_code}) is complete.\n\n"
        f"Find the material here:\n{request_obj.content_links or '(no link provided)'}",
        in_reply_to=email_service.thread_id_for(task.ref_code),
    )
    log_activity(
        "coverage-shared",
        request_obj=request_obj,
        ref_code=task.ref_code,
        actor=task.email,
        detail="Drive link shared with the requesting club",
    )


def _award_completion_points(task, request_obj) -> None:
    """
    Credit a completed task's points, adjusted for how promptly it was
    delivered (docs/PRD.md §5.7) — awarded on completion, not on
    assignment/confirmation, so nobody is credited for work not yet done.

    Guarded two ways: `points_awarded` makes this once-only (idempotent
    against a retry after a partial failure), and an unassigned task has
    nobody to credit. The Event Coordinator role isn't a timed deliverable —
    it coordinates others rather than producing one — so it gets its flat
    base points with no early/late modifier.
    """
    if task.points_awarded or not task.email or task.task == TASK_SUPERVISOR:
        return

    base = task.points or 0
    turnaround_hours: float | None = None

    if task.task == TASK_EVENT_COORDINATOR:
        final = base
    else:
        reference = task.event_end if task.req_type == RequestType.COVERAGE else task.created_at
        if reference is None:
            final = base  # nothing to measure turnaround against
        else:
            completed_at = task.completed_at or timezone.now()
            turnaround_hours = (completed_at - reference).total_seconds() / HOUR
            final = final_points(base, turnaround_hours, get_points_scheme())

    task.points = final
    task.points_awarded = True
    task.timing_applied = True
    task.save(update_fields=["points", "points_awarded", "timing_applied"])

    if final:
        award_points(task.email, final)

    detail = f"{task.task}: +{final} pts"
    if turnaround_hours is not None:
        detail += f" (turnaround {round(turnaround_hours)}h)"
    log_activity(
        "points-awarded",
        request_obj=request_obj,
        ref_code=task.ref_code,
        member=task.email,
        detail=detail,
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
    Find overdue confirmed tasks, mark them LATE and tell the people who need
    to know (docs/PRD.md §5.8).

    This no longer issues strikes: yellow and red strikes are handed out by
    hand, by a vertical head or the POC/Admin, who decide whether a late task
    deserves one. The sweep's job is only to flag the task and make sure the
    assignee, the Event Coordinator, the Task Supervisor and the committee
    head all hear about it.

    Driven hourly by Windows Task Scheduler via `manage.py deadline_check`.
    Returns a summary so the command can report it.
    """
    settings = get_settings()
    now = timezone.now()

    overdue = list(
        Task.objects.filter(status=TaskStatus.CONFIRMED, deadline__lt=now, struck=False)
        # Neither the coordinator nor the supervisor is a deliverable.
        .exclude(task__in=[TASK_EVENT_COORDINATOR, TASK_SUPERVISOR])
        .select_related("request")
    )

    if not overdue:
        logger.info("deadline check: nothing overdue")
        return {"late": 0, "checked_at": now}

    late_count = 0
    for task in overdue:
        coordinator = (task.coordinator_email or "").lower()
        supervisor = (task.request.supervisor_email or "").lower()
        assignee = (task.email or "").lower()
        try:
            with transaction.atomic():
                # Re-check under lock: `overdue` was read without one, so an
                # overlapping run (a manual invocation racing the scheduled
                # one) may already have handled this exact task by the time
                # this transaction starts.
                locked = Task.objects.select_for_update().get(pk=task.pk)
                if locked.struck or locked.status != TaskStatus.CONFIRMED:
                    continue
                locked.status = TaskStatus.LATE
                locked.struck = True
                locked.save(update_fields=["status", "struck"])
            late_count += 1
        except Exception as exc:
            logger.error("deadline check failed for task %s: %s", task.pk, exc)
            continue

        recipients = list(
            dict.fromkeys(
                address
                for address in (assignee, coordinator, supervisor, settings.head_email or "")
                if address
            )
        )
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

    logger.info("deadline check: marked %d task(s) LATE", late_count)
    return {"late": late_count, "checked_at": now}
