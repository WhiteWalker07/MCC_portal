"""
Pipeline builder — turns a request plus the task-type config into the list of
tasks to assign (docs/PRD.md §5.2).

Ported from `server/src/engine/pipeline.ts`. Pure: it takes the request and the
task types as arguments and returns plain values, so it can be tested without
touching the database.

    Coverage -> the requested shoot roles, then DERIVE a Photo Editor (if a
                Photographer was asked for) and a Video Editor (if a
                Videographer was), then the Event Coordinator (any first-year),
                then the Task Supervisor (a second-year).
    Post     -> Content Writer (writes the caption) + Graphic Designer (builds
                the post from the submitted content/proofs -- `content_links`).
                There is no vetting step and no supervisor on a Post; the
                Graphic Designs head can change the auto-picked Graphic
                Designer from Assignments.

The Event Coordinator is staffed after the skilled roles on purpose: it needs no
skill, so if it went first it would use up the fairest first-year before the
photographer or editor -- who *do* need a specific skill -- got their pick.

Deadlines: Coverage deliverables are measured from the event end plus the task
type's SLA; at-event roles (sla_hours 0) are due when the event ends. A Post
request, or a Coverage request with no end time, is measured from now. The
Task Supervisor has no deadline at all.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from django.utils import timezone

from core.constants import (
    COORDINATOR_GRACE_HOURS,
    DERIVED_EDITOR,
    TASK_CONTENT_WRITER,
    TASK_EVENT_COORDINATOR,
    TASK_GRAPHIC_DESIGNER,
    TASK_SUPERVISOR,
    RequestType,
    TaskStatus,
)

from .points import base_points_for


@dataclass(frozen=True)
class PipelineTask:
    """One task to create, before it becomes a Task row."""

    task: str
    required_skill: str
    points: int
    sla_hours: int
    at_event: bool
    vertical: str
    deadline: datetime | None
    #: For an editing task: the shoot role whose work it edits ("Photographer").
    #: The editor is given to whoever holds that shooter task.
    pairs_with: str = ""


def build_pipeline(request_obj, task_types, now: datetime, scheme) -> list[PipelineTask]:
    by_name = {t.task: t for t in task_types}
    names: list[str] = []

    if request_obj.type == RequestType.COVERAGE:
        roles = list(request_obj.roles_needed or [])
        for role in roles:
            if role in by_name and role not in names:
                names.append(role)
        # Editors are derived in a second pass so they always follow the shoot
        # roles they come from, whatever order the requester ticked them.
        for role in roles:
            derived = DERIVED_EDITOR.get(role)
            if derived and derived in by_name and derived not in names:
                names.append(derived)
        names.append(TASK_EVENT_COORDINATOR)
        names.append(TASK_SUPERVISOR)
    else:
        names.append(TASK_CONTENT_WRITER)
        names.append(TASK_GRAPHIC_DESIGNER)

    editor_of = {editor: shooter for shooter, editor in DERIVED_EDITOR.items()}

    pipeline: list[PipelineTask] = []
    for name in names:
        task_type = by_name.get(name)
        if task_type is None:
            # Config is missing this task type — skip rather than fail the whole
            # request. The missing role simply won't be staffed.
            continue
        pipeline.append(
            PipelineTask(
                task=task_type.task,
                required_skill=task_type.required_skill,
                points=base_points_for(task_type.task, scheme),
                sla_hours=task_type.sla_hours,
                at_event=task_type.at_event,
                vertical=task_type.vertical or "",
                deadline=compute_deadline(task_type, request_obj, now),
                pairs_with=editor_of.get(task_type.task, ""),
            )
        )

    # The Event Coordinator is due after everyone else, so its deadline depends
    # on the rest of the pipeline and can only be set once that is built.
    if request_obj.type == RequestType.COVERAGE:
        others = [p.deadline for p in pipeline if p.task not in (TASK_EVENT_COORDINATOR, TASK_SUPERVISOR)]
        for i, p in enumerate(pipeline):
            if p.task == TASK_EVENT_COORDINATOR:
                pipeline[i] = replace(p, deadline=coordinator_deadline(request_obj, others))
    return pipeline


def coordinator_deadline(request_obj, other_deadlines) -> datetime | None:
    """
    When the Event Coordinator is due: `COORDINATOR_GRACE_HOURS` after the last
    deadline of the request's other individual tasks, or after the event end if
    there are none. `None` when the request has no event end to measure from.
    """
    known = [d for d in other_deadlines if d is not None]
    latest = max(known) if known else request_obj.event_end
    if latest is None:
        return None
    return latest + timedelta(hours=COORDINATOR_GRACE_HOURS)


def refresh_coordinator_deadline(request_obj) -> None:
    """
    Recompute the Event Coordinator's deadline from the request's other tasks.
    Call it whenever a task is added or the other deadlines move. A coordinator
    who has already finished keeps the deadline it was judged against. One
    marked LATE whose deadline has now moved into the future is watched again.
    """
    if request_obj.type != RequestType.COVERAGE:
        return
    tasks = list(request_obj.tasks.all())
    others = [t.deadline for t in tasks if t.task not in (TASK_EVENT_COORDINATOR, TASK_SUPERVISOR)]
    new_deadline = coordinator_deadline(request_obj, others)
    now = timezone.now()
    for task in tasks:
        if task.task != TASK_EVENT_COORDINATOR or task.status == TaskStatus.DONE:
            continue
        fields = []
        if task.deadline != new_deadline:
            task.deadline = new_deadline
            fields.append("deadline")
        if task.status == TaskStatus.LATE and new_deadline and new_deadline > now:
            # Any strike already given stands, as for a moved event time.
            task.status = TaskStatus.CONFIRMED
            task.struck = False
            fields += ["status", "struck"]
        if fields:
            task.save(update_fields=fields)


def compute_deadline(task_type, request_obj, now: datetime) -> datetime | None:
    """When a task of this type, on this request, is due. `None` = no deadline."""
    if task_type.task == TASK_SUPERVISOR:
        return None  # supervising has no due date; it closes with the Event Coordinator
    if task_type.task == TASK_EVENT_COORDINATOR:
        if request_obj.type != RequestType.COVERAGE:
            return None  # a coordinator added to a Post has no event to be timed against
        # A first guess of "event end + grace". Callers that know the rest of the
        # request's tasks refine it via `coordinator_deadline`.
        end = request_obj.event_end
        return end + timedelta(hours=COORDINATOR_GRACE_HOURS) if end else None
    if request_obj.type == RequestType.COVERAGE and request_obj.event_end:
        end = request_obj.event_end
        if task_type.at_event and task_type.sla_hours == 0:
            return end
        return end + timedelta(hours=task_type.sla_hours)
    # Post, or Coverage with no end time: measure from now.
    return now + timedelta(hours=task_type.sla_hours)
