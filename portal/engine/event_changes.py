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
from core.config import get_settings, get_task_types
from core.constants import TASK_POST, TASK_SUPERVISOR, RequestStatus, TaskStatus
from services import email as email_service
from services.calendar import calendar_service

from .pipeline import compute_deadline, refresh_coordinator_deadline

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


def notify_subevent_change(request_obj, action: str, sub, actor: str) -> None:
    """
    Tell the Event Coordinator and the POC that the schedule changed after
    submission. `action` is "added", "edited" or "deleted". Before the request is
    accepted the coordinator is only a proposal nobody has been told about, so
    then only the POC hears.
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
