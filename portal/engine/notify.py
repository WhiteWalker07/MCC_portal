"""
Notification side effects shared by the assignment paths.

Split out of the old `engine/assignment.ts` so that confirm.py, assignment.py
and workflow.py can all reach them without importing each other.
"""

from __future__ import annotations

from django.db.models import F
from django.utils import timezone

from core.constants import TASK_SUPERVISOR
from core.models import TeamMember
from services import email as email_service
from services.calendar import calendar_service


def award_points(member_email: str, delta: int) -> None:
    """Move a member's score by `delta`. Negative removes points."""
    if not member_email or not delta:
        return
    TeamMember.objects.filter(email=member_email.strip().lower()).update(
        points=F("points") + delta
    )


def notify_assignee(task) -> None:
    """
    Put the work on the assignee's calendar and tell them about it.

    An at-event task books the event window itself; anything else books a
    reminder at its deadline.
    """
    if not task.email:
        return

    calendar = calendar_service()
    label = task.label
    # A multi-day event is whole days with no single window, so a task that covers
    # the whole of it gets the deadline reminder instead of a hold. A task that
    # covers one sub-event has that sub-event's real time, so it does get a hold.
    has_window = task.event_start and task.event_end and (task.sub_event_id or not task.request.is_multiday)
    if task.at_event and has_window:
        calendar.create_hold(
            email=task.email,
            title=f"{task.ref_code} {label} — {task.event_name}",
            start=task.event_start,
            end=task.event_end,
            description=task.venue or "",
        )
    elif task.deadline:
        calendar.create_reminder(
            email=task.email,
            title=f"{task.ref_code} {label} due — {task.event_name}",
            due=task.deadline,
        )

    if task.task == TASK_SUPERVISOR:
        body = (
            f"You've been assigned as Task Supervisor for {task.event_name} ({task.ref_code}).\n\n"
            "You oversee this event's team. You'll be copied on any late-task notice for it, "
            "and this closes on its own once the Event Coordinator marks the event done. "
            "Only the POC or Admin can change who supervises.\n"
        )
    else:
        body = f"You've been assigned as {task.task} for {task.event_name} ({task.ref_code}).\n"
        if task.sub_event_id:
            sub = task.sub_event
            body += (
                f"\nYou are covering the sub-event \"{sub.name}\": "
                f"{timezone.localtime(sub.start):%a %d %b, %H:%M}–{timezone.localtime(sub.end):%H:%M}"
                + (f", {sub.venue}" if sub.venue else "")
                + ".\n\n"
            )
    if task.deadline:
        body += f"Deadline: {task.deadline:%d %b %Y, %H:%M}\n"
    email_service.send(
        task.email,
        f"[Assigned] {task.ref_code} {label} — {task.event_name}",
        body,
    )
