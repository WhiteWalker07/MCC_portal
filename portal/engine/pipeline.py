"""
Pipeline builder — turns a request plus the task-type config into the list of
tasks to assign (docs/PRD.md §5.2).

Ported from `server/src/engine/pipeline.ts`. Pure: it takes the request and the
task types as arguments and returns plain values, so it can be tested without
touching the database.

    Coverage -> Event Coordinator + the requested shoot roles, then DERIVE a
                Photo Editor (if a Photographer was asked for) and a Video
                Editor (if a Videographer was).
    Post     -> Vetter.

Deadlines: Coverage deliverables are measured from the event end plus the task
type's SLA; at-event roles (sla_hours 0) are due when the event ends. A Post
request, or a Coverage request with no end time, is measured from now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from core.constants import DERIVED_EDITOR, RequestType, TASK_EVENT_COORDINATOR, TASK_VETTER

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


def build_pipeline(request_obj, task_types, now: datetime, scheme) -> list[PipelineTask]:
    by_name = {t.task: t for t in task_types}
    names: list[str] = []

    if request_obj.type == RequestType.COVERAGE:
        names.append(TASK_EVENT_COORDINATOR)
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
    else:
        names.append(TASK_VETTER)

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
            )
        )
    return pipeline


def compute_deadline(task_type, request_obj, now: datetime) -> datetime | None:
    """When a task of this type, on this request, is due."""
    if request_obj.type == RequestType.COVERAGE and request_obj.event_end:
        end = request_obj.event_end
        if task_type.at_event and task_type.sla_hours == 0:
            return end
        return end + timedelta(hours=task_type.sla_hours)
    # Post, or Coverage with no end time: measure from now.
    return now + timedelta(hours=task_type.sla_hours)
