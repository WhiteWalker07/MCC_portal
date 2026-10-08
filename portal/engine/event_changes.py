"""
Changes a club makes to a Coverage request after it was submitted: moving the
event's time, and adding/editing/removing sub-events.

Kept out of the views so the rules (what gets recomputed, who is told, what a
calendar clash means) are testable without a request/response cycle.

**What a time change touches.** The event's start and end move (never their
dates — the form guarantees that). Every task carries denormalised copies of the
event window, so those are updated too, and each unfinished task's deadline is
recomputed from the new end. The team is *kept as it is*: nobody is reassigned,
but anyone whose calendar shows a clash in the newly covered time is flagged to
the POC and the Task Supervisor, who can then reassign if it matters.

**Calendar holds cannot be moved.** services/calendar.py only creates events
(it stores no event id), so the hold made when someone was assigned stays on
their calendar at the old time. New holds are created for the new window and the
email says plainly that the old entry is stale.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from core.activity import log_activity
from core.config import get_points_scheme, get_settings, get_task_types, get_team
from core.constants import (
    DERIVED_EDITOR,
    ROSTER_ROLES,
    TASK_POST,
    TASK_SUPERVISOR,
    RequestStatus,
    RequestType,
    TaskStatus,
)
from core.models import task_label
from services import email as email_service
from services.calendar import calendar_service

from .notify import notify_assignee
from .pipeline import build_pipeline, compute_deadline, refresh_coordinator_deadline

#: Task statuses of people who have actually been assigned (as opposed to merely
#: proposed by the engine, or unfilled).
TOLD_STATUSES = (TaskStatus.CONFIRMED, TaskStatus.LATE, TaskStatus.DONE)


def _fmt(value: datetime) -> str:
    return timezone.localtime(value).strftime("%d %b %Y, %H:%M")


def _uncovered_segments(old_start, old_end, new_start, new_end):
    """
    The parts of the new window the old one didn't already cover.

    A member's own hold for the old window shows up as "busy" in a free/busy
    query, and a same-day move nearly always overlaps it — checking the whole
    new window would flag everybody as clashing with themselves. Only the time
    that wasn't already spoken for can reveal a genuinely new clash.
    """
    segments = []
    if new_start < old_start:
        segments.append((new_start, min(new_end, old_start)))
    if new_end > old_end:
        segments.append((max(new_start, old_end), new_end))
    return [(a, b) for a, b in segments if a < b]


def apply_event_time_change(request_obj, new_start, new_end, actor: str) -> dict:
    """
    Move a Coverage request's event to `new_start`–`new_end` and tell everyone.

    Returns `{"clashes": [(member name, email), ...]}` so the caller can mention
    them. The caller has already validated permission, the 24-hour cutoff and
    that the dates did not change.
    """
    old_start, old_end = request_obj.event_start, request_obj.event_end
    task_types = {t.task: t for t in get_task_types()}
    now = timezone.now()
    reminders: list = []

    with transaction.atomic():
        request_obj.event_start = new_start
        request_obj.event_end = new_end
        request_obj.save(update_fields=["event_start", "event_end"])

        tasks = list(request_obj.tasks.select_for_update())
        for task in tasks:
            task.event_start = new_start
            task.event_end = new_end
            fields = ["event_start", "event_end"]

            open_task = task.status != TaskStatus.DONE and task.task not in (TASK_SUPERVISOR, TASK_POST)
            if open_task:
                task_type = task_types.get(task.task)
                previous = task.deadline
                if task_type is not None:
                    task.deadline = compute_deadline(task_type, request_obj, now)
                elif task.deadline and old_end:
                    # A task type someone since deleted from config: keep its
                    # distance from the event's end instead of losing the deadline.
                    task.deadline = task.deadline + (new_end - old_end)
                if task.status == TaskStatus.LATE and task.deadline and task.deadline > now:
                    # Moved later than "now": it isn't overdue any more, so it
                    # goes back to being watched (any strike already given stands).
                    task.status = TaskStatus.CONFIRMED
                    task.struck = False
                    fields += ["status", "struck"]
                if task.deadline != previous:
                    fields.append("deadline")
                    if task.email and not task.at_event and task.deadline:
                        reminders.append(task)
            task.save(update_fields=fields)

        # The coordinator is due after the last of the others, whose deadlines
        # just moved.
        refresh_coordinator_deadline(request_obj)

    # Until the request is accepted nobody has been told they're on it (their
    # task is still PROPOSED), so there's no team to email, no calendar entry to
    # add and no clash worth flagging — only the POC, who is about to decide.
    told = request_obj.status in RequestStatus.CONFIRMED_STATES
    informed = [
        t for t in tasks if told and t.email and t.status in TOLD_STATUSES and t.task != TASK_POST
    ]

    calendar = calendar_service()
    clashes: list[tuple[str, str]] = []
    segments = _uncovered_segments(old_start, old_end, new_start, new_end) if old_start and old_end else []

    # Everyone is checked *before* anyone is given a hold: a hold made for one of
    # a person's tasks would otherwise show as busy when their next task (a
    # small team lets one person double up) is checked, flagging them as
    # clashing with themselves. One hold per person, however many tasks.
    at_event_tasks = {}
    for task in informed:
        if task.at_event:
            at_event_tasks.setdefault(task.email.lower(), []).append(task)

    for address, held in at_event_tasks.items():
        if any(not calendar.is_free(address, a, b) for a, b in segments):
            clashes.append((held[0].member, held[0].email))

    for address, held in at_event_tasks.items():
        first = held[0]
        calendar.create_hold(
            email=first.email,
            title=f"{first.ref_code} {' + '.join(t.task for t in held)} — {first.event_name} (new time)",
            start=new_start,
            end=new_end,
            description=first.venue or "",
        )
    for task in reminders:
        if task in informed:
            calendar.create_reminder(
                email=task.email,
                title=f"{task.ref_code} {task.task} due — {task.event_name}",
                due=task.deadline,
            )

    settings = get_settings()
    poc = list(settings.secretary_emails or [])
    ref = request_obj.ref_code
    thread = email_service.thread_id_for(ref)

    recipients = [t.email for t in informed] + [request_obj.supervisor_email if told else ""] + poc
    email_service.send(
        recipients,
        f"[Time changed] {ref} — {request_obj.event_name}",
        f"The time of {request_obj.event_name} ({ref}) has been changed by the requesting body.\n\n"
        f"Was: {_fmt(old_start)} – {_fmt(old_end)}\n"
        f"Now: {_fmt(new_start)} – {_fmt(new_end)}\n"
        f"Venue: {request_obj.venue or 'unchanged'}\n\n"
        "The date is unchanged. If this event is on your calendar, please update it: the portal "
        "can't edit an entry it already made, so it may still show the old time (where calendar "
        "sync is on, a new entry for the new time is added as well). Your task deadlines have "
        "been recalculated from the new end time.\n"
        "If you can no longer make the new time, tell the POC or your Task Supervisor.",
        in_reply_to=thread,
    )

    if clashes:
        email_service.send(
            [request_obj.supervisor_email] + poc,
            f"[Calendar clash] {ref} — {request_obj.event_name}",
            f"The new time for {request_obj.event_name} ({ref}) overlaps something else on the "
            "calendar of:\n"
            + "\n".join(f"  {name} <{email}>" for name, email in clashes)
            + "\n\nThey are still assigned. Reassign from the Assignments page if they can't "
            "make it.",
            in_reply_to=thread,
        )

    log_activity(
        "event-time-changed",
        request_obj=request_obj,
        ref_code=ref,
        actor=actor,
        detail=f"{_fmt(old_start)}–{_fmt(old_end)} -> {_fmt(new_start)}–{_fmt(new_end)}"
        + (f"; {len(clashes)} calendar clash(es)" if clashes else ""),
    )
    return {"clashes": clashes}


# ── A sub-event's own team (multi-day events) ───────────────────────────────


def _club_team_update(request_obj, lines: list[str]) -> None:
    """Tell the requesting club its team changed (only once it has been given a team)."""
    if lines and request_obj.status in RequestStatus.CONFIRMED_STATES:
        email_service.send(
            request_obj.contact_email,
            f"[Team update] {request_obj.ref_code} — {request_obj.event_name}",
            "\n".join(lines),
            in_reply_to=email_service.thread_id_for(request_obj.ref_code),
        )


def staff_new_sub_event(request_obj, sub, actor: str) -> list:
    """
    Give a sub-event added after the request was submitted its own photographer and
    videographer (whichever the club ticked), picked the usual way, each with their
    own editing. Only a multi-day Coverage event has per-sub-event teams.

    Once the request is accepted the people are confirmed and emailed at once, the
    team list the club holds gains them, and the club is told; before that they are
    proposals the POC's approval will confirm. The POC or the coordinator can change
    anyone from Assignments. Returns the tasks created.
    """
    if request_obj.type != RequestType.COVERAGE or not request_obj.is_multiday:
        return []
    # A role the event already covers as a whole (it was submitted without sub-events,
    # so it has one photographer for all of it) isn't doubled up per sub-event; a role
    # already staffed per sub-event, or with nobody left on it, is.
    whole_event_roles = {
        role
        for role in DERIVED_EDITOR
        if (tasks := list(request_obj.tasks.filter(task=role))) and not any(t.sub_event_id for t in tasks)
    }
    pipeline = [
        p
        for p in build_pipeline(
            request_obj, get_task_types(), timezone.now(), get_points_scheme(), sub_events=[sub]
        )
        if p.sub_event is not None and _shoot_role(p.task) not in whole_event_roles
    ]
    return _staff_and_announce(request_obj, pipeline, actor)


def _shoot_role(task_name: str) -> str:
    """The shoot a task belongs to: itself for a shooter, the shooter for an editor."""
    editor_of = {editor: shooter for shooter, editor in DERIVED_EDITOR.items()}
    return task_name if task_name in DERIVED_EDITOR else editor_of.get(task_name, "")


def _staff_and_announce(request_obj, pipeline, actor: str) -> list:
    """
    Staff `pipeline` on an existing request the usual way and save it. Once the
    request is accepted the people are confirmed and emailed at once, the club's team
    list gains them and the club is told; before that they are proposals the POC's
    approval will confirm. Returns the tasks created.
    """
    from .workflow import create_task_row, staff_pipeline

    if not pipeline:
        return []
    accepted = request_obj.status in RequestStatus.CONFIRMED_STATES
    now = timezone.now()
    staffed = staff_pipeline(request_obj, pipeline, get_settings(), get_team(), calendar_service())
    created: dict[str, object] = {}
    with transaction.atomic():
        for item in staffed:
            task = create_task_row(
                request_obj, item.pipeline_task, item.member, item.reason,
                ref_code=request_obj.ref_code, coordinator_email=request_obj.coordinator_email, now=now,
                paired=created.get(item.pipeline_task.pairs_with) if item.pipeline_task.pairs_with else None,
            )
            if accepted and item.member is not None:
                task.status = TaskStatus.CONFIRMED
                task.save(update_fields=["status"])
            created[item.pipeline_task.ident] = task
        tasks = list(created.values())
        if accepted:
            roster = list(request_obj.roster or [])
            roster.extend(
                {"role": t.label, "name": t.member, "email": t.email, "phone": t.phone or ""}
                for t in tasks
                if t.email and t.task in ROSTER_ROLES
            )
            request_obj.roster = roster
            request_obj.save(update_fields=["roster"])
        refresh_coordinator_deadline(request_obj)

    if accepted:
        for task in tasks:
            notify_assignee(task)
        _club_team_update(
            request_obj,
            [f"{t.label} for {request_obj.event_name} ({request_obj.ref_code}) is {t.member} <{t.email}>"
             + (f" · {t.phone}" if t.phone else "")
             for t in tasks if t.email and t.task in ROSTER_ROLES],
        )
    for t in tasks:
        log_activity(
            "proposed" if t.email else "unfilled",
            request_obj=request_obj,
            ref_code=request_obj.ref_code,
            actor=actor,
            member=t.email,
            detail=f"{t.label} -> {t.member}" if t.email else f"{t.label} UNFILLED: {t.reason}",
        )
    return tasks


def team_note_for(tasks, heading: str = "Team assigned to it:") -> str:
    """One line per task for the schedule email: who now covers the new sub-event."""
    if not tasks:
        return ""
    lines = [f"  {t.label}: {t.member} <{t.email}>" if t.email else f"  {t.label}: UNFILLED ({t.reason})" for t in tasks]
    return f"{heading}\n" + "\n".join(lines) + "\nChange anyone from the Assignments page."


def retime_sub_event(sub, old: tuple, actor: str) -> list[tuple[str, str]]:
    """
    A sub-event's time, venue or name changed: move its own team's windows and
    deadlines with it and tell them. `old` is (name, start, end, venue) from before
    the change. Done tasks are left as they were.

    Returns `[(member name, email), ...]` for anyone on the team whose calendar is
    busy in the part of the new time the old one didn't cover; the Task Supervisor
    and the POC are told about them, as for a change of the whole event's time.
    """
    request_obj = sub.request
    old_name, old_start, old_end, old_venue = old
    moved = (sub.start, sub.end) != (old_start, old_end)
    task_types = {t.task: t for t in get_task_types()}
    now = timezone.now()
    told: dict[str, list] = {}

    with transaction.atomic():
        all_tasks = list(sub.tasks.select_for_update())
        tasks = [t for t in all_tasks if t.status != TaskStatus.DONE]
        for task in tasks:
            fields = ["event_start", "event_end", "venue"]
            task.event_start, task.event_end, task.venue = sub.start, sub.end, sub.venue or ""
            task_type = task_types.get(task.task)
            if task_type is not None:
                task.deadline = compute_deadline(task_type, request_obj, now, window_end=sub.end)
                fields.append("deadline")
                if task.status == TaskStatus.LATE and task.deadline > now:
                    task.status, task.struck = TaskStatus.CONFIRMED, False
                    fields += ["status", "struck"]
            task.save(update_fields=fields)
            if task.email and task.status in TOLD_STATUSES:
                told.setdefault(task.email, []).append(task)
        if sub.name != old_name and request_obj.roster:
            # Only this sub-event's own people are renamed: matched by the exact
            # role and email each of its tasks put on the roster, so another
            # sub-event with a similar (or the same) name is left alone.
            renamed = {
                (task_label(t.task, old_name), (t.email or "").lower()): task_label(t.task, sub.name)
                for t in all_tasks
            }
            roster = []
            for entry in request_obj.roster:
                new_role = renamed.get((entry.get("role", ""), (entry.get("email") or "").lower()))
                roster.append({**entry, "role": new_role} if new_role else entry)
            request_obj.roster = roster
            request_obj.save(update_fields=["roster"])
        refresh_coordinator_deadline(request_obj)

    calendar = calendar_service()
    clashes: list[tuple[str, str]] = []
    if moved:
        # Checked before anyone gets a new hold, and only for the time the old
        # window didn't cover: their own hold for the old time would otherwise
        # make everybody look busy (see apply_event_time_change).
        segments = _uncovered_segments(old_start, old_end, sub.start, sub.end)
        for email, held in told.items():
            if held[0].at_event and any(not calendar.is_free(email, a, b) for a, b in segments):
                clashes.append((held[0].member, email))

    if moved or (sub.venue or "") != (old_venue or ""):
        for email, held in told.items():
            first = held[0]
            if moved and first.at_event:
                calendar.create_hold(
                    email=email, title=f"{request_obj.ref_code} {first.label} — {request_obj.event_name} (new time)",
                    start=sub.start, end=sub.end, description=sub.venue or "",
                )
            email_service.send(
                email,
                f"[Schedule updated] {request_obj.ref_code} {sub.name}",
                f"The sub-event \"{sub.name}\" of {request_obj.event_name} ({request_obj.ref_code}), which you cover, changed.\n\n"
                f"Was: {_fmt(old_start)} – {timezone.localtime(old_end):%H:%M}"
                + (f" · {old_venue}" if old_venue else "")
                + f"\nNow: {_fmt(sub.start)} – {timezone.localtime(sub.end):%H:%M}"
                + (f" · {sub.venue}" if sub.venue else "")
                + "\n\nYour deadline has been recalculated. The portal can't edit a calendar entry it already made, "
                "so an old one may still show the previous time.",
                in_reply_to=email_service.thread_id_for(request_obj.ref_code),
            )

    if clashes:
        email_service.send(
            [request_obj.supervisor_email] + list(get_settings().secretary_emails or []),
            f"[Calendar clash] {request_obj.ref_code} — {request_obj.event_name}",
            f"The new time for the sub-event \"{sub.name}\" of {request_obj.event_name} "
            f"({request_obj.ref_code}) overlaps something else on the calendar of:\n"
            + "\n".join(f"  {name} <{email}>" for name, email in clashes)
            + "\n\nThey are still assigned. Reassign from the Assignments page if they can't make it.",
            in_reply_to=email_service.thread_id_for(request_obj.ref_code),
        )
    return clashes


def release_sub_event(sub, actor: str) -> str:
    """
    A sub-event is being deleted: take its own team off it (done work is kept), tell
    them, and update the club's team list. If it was the last sub-event, a ticked
    shoot role left with nobody on it is staffed for the whole event again. Returns
    the text for the schedule email. Call it before deleting the sub-event.
    """
    request_obj = sub.request
    with transaction.atomic():
        tasks = list(sub.tasks.select_for_update().all())
        gone = [t for t in tasks if t.status != TaskStatus.DONE]
        snapshot = [(t.label, t.email, t.member, t.status, t.task in ROSTER_ROLES) for t in gone]
        for t in gone:
            t.delete()
        if request_obj.roster and any(s[4] for s in snapshot):
            roster = list(request_obj.roster)
            for label, email, _, _, in_roster in snapshot:
                if in_roster:
                    entry = next((e for e in roster if e.get("role") == label and e.get("email") == email), None)
                    if entry is not None:
                        roster.remove(entry)
            request_obj.roster = roster
            request_obj.save(update_fields=["roster"])
        refresh_coordinator_deadline(request_obj)

    told: dict[str, list[str]] = {}
    for label, email, _, status, _ in snapshot:
        if email and status in (TaskStatus.CONFIRMED, TaskStatus.LATE):
            told.setdefault(email, []).append(label)
    for email, labels in told.items():
        email_service.send(
            email,
            f"[Schedule updated] {request_obj.ref_code} {sub.name} cancelled",
            f"The sub-event \"{sub.name}\" of {request_obj.event_name} ({request_obj.ref_code}) was cancelled, "
            "so you are no longer needed for it:\n" + "\n".join(f"  {label}" for label in labels) + "\n",
            in_reply_to=email_service.thread_id_for(request_obj.ref_code),
        )
    _club_team_update(
        request_obj,
        [f"{label} for {request_obj.event_name} ({request_obj.ref_code}) is no longer needed: "
         f"the sub-event \"{sub.name}\" was cancelled."
         for label, _, _, _, in_roster in snapshot if in_roster],
    )

    # With its last sub-event gone the event is covered as a whole again, the way it
    # would have been had it been submitted without sub-events: a ticked shoot role
    # nobody is left on gets one photographer/videographer for the whole event.
    # Only roles this sub-event itself covered: a role someone deliberately took off
    # the event earlier stays off.
    restored = []
    if not request_obj.sub_events.exclude(pk=sub.pk).exists():
        uncovered = {
            role
            for role in {t.task for t in tasks if t.task in DERIVED_EDITOR}
            if not request_obj.tasks.filter(task=role).exclude(sub_event=sub).exists()
        }
        if uncovered:
            pipeline = [
                p
                for p in build_pipeline(request_obj, get_task_types(), timezone.now(), get_points_scheme())
                if p.sub_event is None and _shoot_role(p.task) in uncovered
            ]
            restored = _staff_and_announce(request_obj, pipeline, actor)

    # What was taken off may have been the last deliverable the event was waiting for.
    from .workflow import advance_if_covered

    advance_if_covered(request_obj)

    lines = []
    if snapshot:
        lines.append("Its own team was released: " + ", ".join(label for label, *_ in snapshot) + ".")
    if restored:
        lines.append(
            "No sub-events are left, so the event is covered as a whole again.\n"
            + team_note_for(restored, heading="Assigned to the whole event:")
        )
    return "\n".join(lines)


def notify_subevent_change(request_obj, action: str, sub, actor: str, team_note: str = "") -> None:
    """
    Tell the Event Coordinator and the POC that the schedule changed after
    submission. `action` is "added", "edited" or "deleted". Before the request is
    accepted the coordinator is only a proposal nobody has been told about, so
    then only the POC hears. `team_note` says what happened to the sub-event's own
    photographer/videographer (who was assigned, or released).
    """
    settings = get_settings()
    ref = request_obj.ref_code
    told = request_obj.status in RequestStatus.CONFIRMED_STATES
    email_service.send(
        [request_obj.coordinator_email if told else ""] + list(settings.secretary_emails or []),
        f"[Schedule updated] {ref} — {request_obj.event_name}",
        f"A sub-event was {action} on {request_obj.event_name} ({ref}) by the requesting body:\n\n"
        f"  {sub.name}\n"
        f"  {_fmt(sub.start)} – {timezone.localtime(sub.end):%H:%M}\n"
        + (f"  Venue: {sub.venue}\n" if sub.venue else "")
        + (f"  Notes: {sub.notes}\n" if sub.notes else "")
        + (f"\n{team_note}\n" if team_note else "")
        + "\nThe full schedule is on the request page.",
        in_reply_to=email_service.thread_id_for(ref),
    )
    log_activity(
        f"subevent-{action}",
        request_obj=request_obj,
        ref_code=ref,
        actor=actor,
        detail=f"{sub.name}: {_fmt(sub.start)}–{timezone.localtime(sub.end):%H:%M}",
    )
