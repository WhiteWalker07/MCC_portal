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
    #: For an editing task: the `ident` of the shoot task whose work it edits. The
    #: editor is given to whoever holds that shooter task.
    pairs_with: str = ""
    #: Unique name for this task within its request. For the ordinary one-of-each
    #: tasks it is just the task name ("Photographer"); a task covering one
    #: sub-event, or an extra one, adds a suffix ("Photographer@s1", "Photographer+1").
    #: Hand picks on the team page are addressed by it. Left blank it means `task`.
    key: str = ""
    #: The sub-event this task covers (saved or not yet saved), if it is one of a
    #: multi-day event's per-sub-event tasks.
    sub_event: object | None = None
    #: (start, end) the person must be free for / the task is about, when that is a
    #: sub-event's own time instead of the whole event's.
    window: tuple | None = None
    #: True for the request's additional Event Coordinator (the main one is not).
    additional: bool = False

    @property
    def ident(self) -> str:
        return self.key or self.task


def ordered_sub_events(sub_events) -> list:
    """
    A stable order for a request's sub-events, used to number them (`@s0`, `@s1`...).
    The same order is used when previewing the team (sub-events not saved yet) and
    when saving, so a person picked for "the second sub-event" stays the second one.
    """
    return sorted(
        sub_events,
        key=lambda s: (s.start, s.end, (s.name or "").lower(), s.venue or "", s.notes or ""),
    )


def task_key(name: str, sub_index: int | None = None, extra_n: int = 0) -> str:
    key = name
    if extra_n:
        key += f"+{extra_n}"
    if sub_index is not None:
        key += f"@s{sub_index}"
    return key


def number_extras(extras) -> list[tuple[str, int | None, int]]:
    """
    Number extra tasks so each gets a unique, repeatable key: [(task, sub_index)]
    becomes [(task, sub_index, n)] where n counts that (task, sub-event) pair from 1.
    """
    seen: dict[tuple, int] = {}
    out = []
    for name, sub_index in extras:
        seen[(name, sub_index)] = seen.get((name, sub_index), 0) + 1
        out.append((name, sub_index, seen[(name, sub_index)]))
    return out


def build_pipeline(
    request_obj, task_types, now: datetime, scheme, *, sub_events=None, extras=None, dropped=None,
    co_coordinator=False,
) -> list[PipelineTask]:
    """
    The tasks a request needs.

    `sub_events` (any sub-events, ordered by `ordered_sub_events`) matter only for a
    multi-day Coverage event: then each shoot role ticked (Photographer, Videographer)
    is created once per sub-event, with its own editing task, each on that
    sub-event's own times. A multi-day event with no sub-events, and every
    single-day event, keep one of each, covering the whole event.

    `extras` is a list of `(task name, sub-event index or None)` for additional tasks
    a person asked for on top (for example a second photographer).

    `dropped` is a set of task keys (`ident`s) the person entering the request chose
    to leave out. Dropping a shoot task drops the editing that follows it; the
    Event Coordinator and Task Supervisor can't be dropped.

    `co_coordinator` asks for an additional Event Coordinator (Coverage only), staffed
    like any task but never the same person as the main one.
    """
    by_name = {t.task: t for t in task_types}
    editor_of = {editor: shooter for shooter, editor in DERIVED_EDITOR.items()}
    coverage = request_obj.type == RequestType.COVERAGE
    subs = list(sub_events or []) if (coverage and request_obj.is_multiday) else []

    # Each entry is (task name, sub-event index or None, extra number, pairs_with key).
    entries: list[tuple[str, int | None, int, str]] = []

    if coverage:
        roles = []
        for role in request_obj.roles_needed or []:
            if role in by_name and role not in roles:
                roles.append(role)
        per_sub = bool(subs) and any(r in DERIVED_EDITOR for r in roles)
        for role in roles:
            if per_sub and role in DERIVED_EDITOR:
                entries.extend((role, i, 0, "") for i in range(len(subs)))
            else:
                entries.append((role, None, 0, ""))
        # Editors are derived in a second pass so they always follow the shoot
        # roles they come from, whatever order the requester ticked them.
        for role in roles:
            derived = DERIVED_EDITOR.get(role)
            if not derived or derived not in by_name or any(e[0] == derived for e in entries):
                continue
            if per_sub:
                entries.extend((derived, i, 0, task_key(role, i)) for i in range(len(subs)))
            else:
                entries.append((derived, None, 0, task_key(role)))
    else:
        entries.append((TASK_CONTENT_WRITER, None, 0, ""))
        entries.append((TASK_GRAPHIC_DESIGNER, None, 0, ""))

    # Additional tasks asked for by hand; an extra shooter brings their own editing.
    for name, sub_index, n in number_extras(extras or []):
        if name not in by_name or name in (TASK_EVENT_COORDINATOR, TASK_SUPERVISOR):
            continue
        if sub_index is not None and not (0 <= sub_index < len(subs)):
            sub_index = None
        entries.append((name, sub_index, n, ""))
        editor = DERIVED_EDITOR.get(name)
        if editor and editor in by_name:
            entries.append((editor, sub_index, n, task_key(name, sub_index, n)))

    if dropped:
        entries = [e for e in entries if task_key(e[0], e[1], e[2]) not in dropped and e[3] not in dropped]

    if coverage:
        entries.append((TASK_EVENT_COORDINATOR, None, 0, ""))
        if co_coordinator:
            entries.append((TASK_EVENT_COORDINATOR, None, 1, ""))
        entries.append((TASK_SUPERVISOR, None, 0, ""))

    pipeline: list[PipelineTask] = []
    for name, sub_index, n, pairs_with in entries:
        task_type = by_name.get(name)
        if task_type is None:
            # Config is missing this task type — skip rather than fail the whole
            # request. The missing role simply won't be staffed.
            continue
        sub = subs[sub_index] if sub_index is not None else None
        pipeline.append(
            PipelineTask(
                task=task_type.task,
                required_skill=task_type.required_skill,
                points=base_points_for(task_type.task, scheme),
                sla_hours=task_type.sla_hours,
                at_event=task_type.at_event,
                vertical=task_type.vertical or "",
                deadline=compute_deadline(task_type, request_obj, now, window_end=sub.end if sub else None),
                pairs_with=pairs_with,
                key=task_key(name, sub_index, n),
                sub_event=sub,
                window=(sub.start, sub.end) if sub else None,
                additional=name == TASK_EVENT_COORDINATOR and n > 0,
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


def compute_deadline(task_type, request_obj, now: datetime, *, window_end=None) -> datetime | None:
    """
    When a task of this type, on this request, is due. `None` = no deadline.

    `window_end` is the end of the sub-event a task covers; its deadline counts from
    there instead of from the end of the whole event.
    """
    if window_end is not None and task_type.task not in (TASK_SUPERVISOR, TASK_EVENT_COORDINATOR):
        if task_type.at_event and task_type.sla_hours == 0:
            return window_end
        return window_end + timedelta(hours=task_type.sla_hours)
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
