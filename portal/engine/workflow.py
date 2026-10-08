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
import math
from dataclasses import dataclass

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
    DERIVED_EDITOR,
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
from core.models import Task, TeamMember, task_label
from services import email as email_service
from services.calendar import calendar_service

from . import leave as leave_sweep
from .assign import Choice, choose_member, choose_supervisor
from .confirm import confirm_request
from .notify import award_points
from .pipeline import PipelineTask, build_pipeline, compute_deadline, ordered_sub_events
from .points import base_points_for, final_points, overdue_multiplier
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


@dataclass(frozen=True)
class Staffed:
    """One pipeline task and who it went to (`member` is None when nobody was eligible)."""

    pipeline_task: PipelineTask
    member: TeamMember | None
    reason: str
    #: True when a person chose this assignee by hand instead of the engine picking.
    chosen: bool = False


def staff_pipeline(
    request_obj, pipeline, settings, team, calendar, preferred: dict[str, TeamMember] | None = None
) -> list[Staffed]:
    """
    Decide who gets each task of `pipeline`. Touches nothing: it only reads, so it
    serves both the real thing (`process_new_request`) and the preview a POC/Admin
    sees before saving a request they are entering for a club (`propose_team`).

    `preferred` maps a task's `ident` (its name, or name plus a sub-event/extra
    suffix) to a person chosen by hand; the caller has already checked they are
    allowed. Everything not in it is picked by the engine as usual. A hand-picked
    person counts as already on the request, so the automatic picks steer around
    them. An editing task with no explicit pick goes to whoever holds its shoot.
    """
    preferred = preferred or {}
    already_assigned: set[str] = {
        m.email for ident, m in preferred.items() if not ident.startswith(TASK_SUPERVISOR)
    }
    open_counts = open_supervision_counts()
    # Who got each shoot task, so its editing task can go to the same person.
    shooter_member: dict[str, TeamMember] = {}
    staffed: list[Staffed] = []

    for pipeline_task in pipeline:
        picked = preferred.get(pipeline_task.ident)
        paired = shooter_member.get(pipeline_task.pairs_with) if pipeline_task.pairs_with else None
        if picked is not None:
            choice = Choice(member=picked, reason="")
        elif pipeline_task.task == TASK_SUPERVISOR:
            choice = choose_supervisor(request_obj, settings, team, open_counts)
        elif paired is not None:
            # Each shooter edits their own work, whether or not they hold the
            # editing skill. (If the shooter is unfilled there's nobody to reuse
            # and the editor is auto-picked below like any other task.)
            choice = Choice(member=paired, reason="")
        else:
            choice = choose_member(
                pipeline_task, request_obj, settings, team, already_assigned, calendar
            )
        member = choice.member
        if member is not None and pipeline_task.task in DERIVED_EDITOR:
            shooter_member[pipeline_task.ident] = member
        if member is not None and pipeline_task.task != TASK_SUPERVISOR:
            already_assigned.add(member.email)
        staffed.append(Staffed(pipeline_task, member, choice.reason, chosen=picked is not None))
    return staffed


def propose_team(
    request_obj, preferred: dict[str, TeamMember] | None = None, *, sub_events=None, extras=None, dropped=None,
    co_coordinator=False,
) -> list[Staffed]:
    """
    The team the engine would pick for an unsaved request, with any hand picks
    applied. Nothing is saved and nobody is emailed. `request_obj` only needs its
    form fields and `campus` (normally filled in when the reference code is
    allocated, so the caller sets it from the club).
    """
    pipeline = build_pipeline(
        request_obj, get_task_types(), timezone.now(), get_points_scheme(),
        sub_events=ordered_sub_events(sub_events or []), extras=extras, dropped=dropped,
        co_coordinator=co_coordinator,
    )
    return staff_pipeline(
        request_obj, pipeline, get_settings(), get_team(), calendar_service(), preferred
    )


def _save_task(request_obj, member, *, sub, task_name, required_skill, at_event, vertical, **fields):
    """
    The one place a `Task` row is built for a request. A task that covers a
    sub-event takes that sub-event's own times and venue; everything else takes the
    whole event's. `fields` carries what differs between callers (points, deadline,
    status, reason, coordinator, pairing...).
    """
    return Task.objects.create(
        request=request_obj,
        req_type=request_obj.type,
        task=task_name,
        required_skill=required_skill,
        at_event=at_event,
        vertical=vertical or "",
        member=member.name if member else "",
        email=member.email if member else "",
        phone=(member.phone or "") if member else "",
        event_name=request_obj.event_name or "",
        event_start=sub.start if sub else request_obj.event_start,
        event_end=sub.end if sub else request_obj.event_end,
        venue=(sub.venue if sub else request_obj.venue) or "",
        sub_event=sub,
        **fields,
    )


def create_task_row(request_obj, pipeline_task, member, reason, *, ref_code, coordinator_email, now, paired=None):
    """Save one staffed pipeline task as a `Task`."""
    # A sub-event not saved yet (a preview) can't be linked; saving always has one.
    sub = pipeline_task.sub_event if getattr(pipeline_task.sub_event, "pk", None) else None
    return _save_task(
        request_obj, member, sub=sub,
        task_name=pipeline_task.task, required_skill=pipeline_task.required_skill,
        at_event=pipeline_task.at_event, vertical=pipeline_task.vertical,
        ref_code=ref_code,
        points=pipeline_task.points,
        deadline=pipeline_task.deadline,
        status=TaskStatus.PROPOSED if member else TaskStatus.UNFILLED,
        reason="" if member else reason,
        coordinator_email=coordinator_email,
        paired_task=paired,
        additional=pipeline_task.additional,
        created_at=now,
    )


def add_task_from_type(
    request_obj, task_type, member, *, sub=None, status, coordinator_email="", paired=None, now=None,
    additional=False,
):
    """
    Save one more task of `task_type` for `member` on an existing request (the
    coordinator or POC adding someone). A task for a sub-event takes that
    sub-event's own times, venue and deadline.
    """
    now = now or timezone.now()
    return _save_task(
        request_obj, member, sub=sub,
        task_name=task_type.task, required_skill=task_type.required_skill,
        at_event=task_type.at_event, vertical=task_type.vertical,
        ref_code=request_obj.ref_code or "",
        # The same scheme automatic assignment uses (coordinator 20, supervisor 0,
        # everything else the domain-task base), not the per-type legacy number.
        points=base_points_for(task_type.task, get_points_scheme()),
        deadline=compute_deadline(task_type, request_obj, now, window_end=sub.end if sub else None),
        status=status,
        coordinator_email=coordinator_email or request_obj.coordinator_email or "",
        paired_task=paired,
        additional=additional,
        created_at=now,
    )


def process_new_request(
    request_obj, *, skip_approval: bool = False, preferred=None, extras=None, dropped=None,
    co_coordinator=False,
) -> None:
    """
    Take a freshly submitted request from 'New' to either 'Pending for POC
    approval' or 'Request Accepted'.

    Allocates the reference code, builds and staffs the task pipeline, then
    applies the approval gate. `skip_approval` is for a request the POC/Admin
    entered on a club's behalf: they are the approver, so it is accepted straight
    away (short-notice Coverage and Posts included). `preferred` is the team they
    chose by hand for that request (task ident -> TeamMember), already validated,
    and `extras` the additional tasks they asked for ([(task name, sub-event index)]);
    `dropped` is the set of task keys they chose to leave out, and `co_coordinator`
    asks for an additional Event Coordinator.

    A multi-day event with sub-events gets its own photographer/videographer (and
    their editing) for each sub-event, so the sub-events must already be saved.
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
    pipeline = build_pipeline(
        request_obj, task_types, now, scheme,
        sub_events=ordered_sub_events(request_obj.sub_events.all()), extras=extras, dropped=dropped,
        co_coordinator=co_coordinator,
    )
    staffed_list = staff_pipeline(request_obj, pipeline, settings, team, calendar, preferred)
    # An additional coordinator nobody is eligible for is simply not created (the team
    # page already told the person entering the request); the POC can add one later.
    staffed_list = [s for s in staffed_list if s.member or not s.pipeline_task.additional]

    coordinator_email = next(
        (
            s.member.email
            for s in staffed_list
            if s.member and s.pipeline_task.task == TASK_EVENT_COORDINATOR and not s.pipeline_task.additional
        ),
        "",
    )
    co_coordinator_email = next((s.member.email for s in staffed_list if s.member and s.pipeline_task.additional), "")
    supervisor_email = next(
        (s.member.email for s in staffed_list if s.member and s.pipeline_task.task == TASK_SUPERVISOR), ""
    )

    # (label, member, reason, chosen by hand)
    outcomes: list[tuple[str, TeamMember | None, str, bool]] = []
    with transaction.atomic():
        created: dict[str, Task] = {}
        for staffed in staffed_list:
            pipeline_task = staffed.pipeline_task
            created[pipeline_task.ident] = create_task_row(
                request_obj, pipeline_task, staffed.member, staffed.reason,
                ref_code=allocation.ref_code, coordinator_email=coordinator_email, now=now,
                paired=created.get(pipeline_task.pairs_with) if pipeline_task.pairs_with else None,
            )
            outcomes.append(
                (
                    task_label(pipeline_task.task, pipeline_task.sub_event.name if pipeline_task.sub_event else ""),
                    staffed.member, staffed.reason, staffed.chosen,
                )
            )
        request_obj.coordinator_email = coordinator_email
        request_obj.supervisor_email = supervisor_email
        request_obj.co_coordinator_email = co_coordinator_email
        request_obj.save(update_fields=["coordinator_email", "supervisor_email", "co_coordinator_email"])

    log_activity(
        "created",
        request_obj=request_obj,
        ref_code=allocation.ref_code,
        actor=request_obj.created_on_behalf_by or request_obj.contact_email,
        detail=f"{request_obj.type} request created"
        + (f" on behalf of {request_obj.contact_email}" if request_obj.created_on_behalf_by else ""),
    )
    for label, member, reason, chosen in outcomes:
        log_activity(
            "proposed" if member else "unfilled",
            request_obj=request_obj,
            ref_code=allocation.ref_code,
            member=member.email if member else "",
            detail=(
                f"{label} -> {member.name}"
                + (f" (chosen by {request_obj.created_on_behalf_by})" if chosen else "")
                if member
                else f"{label} UNFILLED: {reason}"
            ),
        )

    if not skip_approval and _requires_approval(request_obj, settings):
        request_obj.status = RequestStatus.PENDING
        request_obj.save(update_fields=["status"])
        email_service.send(
            settings.secretary_emails,
            f"[Approval needed] {allocation.ref_code} — {request_obj.event_name}",
            _approval_email(request_obj, allocation.ref_code, [(l, m, r) for l, m, r, _ in outcomes]),
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


def advance_if_covered(request_obj) -> None:
    """
    Move an accepted Coverage request to Event Covered once every deliverable is
    done. Run when a task finishes, and when tasks are taken off the request (the
    one removed may have been the last thing it was waiting for).
    """
    if request_obj.type != RequestType.COVERAGE or request_obj.status != RequestStatus.ACCEPTED:
        return
    # Neither the coordinator nor the supervisor is a deliverable.
    deliverables = [
        t
        for t in request_obj.tasks.all()
        if t.task not in (TASK_EVENT_COORDINATOR, TASK_SUPERVISOR) and t.status != TaskStatus.UNFILLED
    ]
    if not deliverables or not all(t.status == TaskStatus.DONE for t in deliverables):
        return
    # Claimed atomically: the last two deliverables finishing at once would
    # otherwise both advance it (and both log it).
    if not _advance(request_obj, RequestStatus.ACCEPTED, RequestStatus.EVENT_COVERED):
        return
    log_activity(
        "event-covered",
        request_obj=request_obj,
        ref_code=request_obj.ref_code,
        actor="engine",
        detail="All coverage deliverables done",
    )


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
        # So does the other coordinator, if the request has two: the hand-off to
        # the club has been made, so there is nothing left for them to hand over.
        _close_other_coordinator(request_obj, task)

    # Only ever advance from 'Request Accepted' — a request that's already
    # covered, ready or posted has moved past this point.
    if request_obj.status != RequestStatus.ACCEPTED:
        return

    if task.req_type == RequestType.COVERAGE:
        advance_if_covered(request_obj)
        return

    tasks = list(request_obj.tasks.all())

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


def _close_other_coordinator(request_obj, finished) -> None:
    """
    When one of a request's two Event Coordinators hands the coverage to the club,
    the other's task is closed too: DONE, with no points, because they did not make
    the hand-off. (Each coordinator is scored on their own task; this one simply
    was not completed by them.) A no-op when the request has only one.
    """
    now = timezone.now()
    for other in request_obj.tasks.filter(task=TASK_EVENT_COORDINATOR).exclude(pk=finished.pk).exclude(
        status__in=[TaskStatus.DONE, TaskStatus.UNFILLED]
    ):
        other.status = TaskStatus.DONE
        other.completed_at = now
        other.points = 0
        other.points_awarded = True  # settled: nothing to credit, and nothing for a retry to credit
        other.save(update_fields=["status", "completed_at", "points", "points_awarded"])
        log_activity(
            "coordinator-closed",
            request_obj=request_obj,
            ref_code=other.ref_code,
            member=other.email,
            actor="engine",
            detail=f"Event Coordinator task closed: {finished.member} handed the coverage to the club",
        )


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
    nobody to credit. The Event Coordinator coordinates others rather than
    producing a deliverable, so it earns no early bonus; it does have a
    deadline of its own, though, and loses points the later it finishes past it.
    """
    if task.points_awarded or not task.email or task.task == TASK_SUPERVISOR:
        return

    base = task.points or 0
    turnaround_hours: float | None = None

    if task.task == TASK_EVENT_COORDINATOR:
        # No early bonus, but the coordinator is timed against its own deadline:
        # finishing after it costs points on the same late curve as a deliverable,
        # counted from that deadline.
        completed_at = task.completed_at or timezone.now()
        if task.deadline and completed_at > task.deadline:
            turnaround_hours = (completed_at - task.deadline).total_seconds() / HOUR
            final = math.floor(base * overdue_multiplier(turnaround_hours, get_points_scheme()) + 0.5)
        else:
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
        label = "overdue" if task.task == TASK_EVENT_COORDINATOR else "turnaround"
        detail += f" ({label} {round(turnaround_hours)}h)"
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

    # Approved Out-of-work leaves start and end by date; ride the same hourly run
    # so no extra scheduled task is needed. Isolated so it can never stop the
    # deadline sweep below.
    try:
        leave_sweep.run_leave_sweep()
    except Exception as exc:
        logger.error("leave sweep failed: %s", exc)

    overdue = list(
        Task.objects.filter(status=TaskStatus.CONFIRMED, deadline__lt=now, struck=False)
        # The supervisor has no deadline. The coordinator does (12h after the
        # request's last individual task), so it's swept like any other task.
        .exclude(task=TASK_SUPERVISOR)
        .select_related("request")
    )

    if not overdue:
        logger.info("deadline check: nothing overdue")
        return {"late": 0, "checked_at": now}

    late_count = 0
    for task in overdue:
        coordinator = (task.coordinator_email or "").lower()
        co_coordinator = (task.request.co_coordinator_email or "").lower()
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
                for address in (assignee, coordinator, co_coordinator, supervisor, settings.head_email or "")
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
