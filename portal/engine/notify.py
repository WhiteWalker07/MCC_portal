"""
Notification side effects shared by the assignment paths.

Split out of the old `engine/assignment.ts` so that confirm.py, assignment.py
and workflow.py can all reach them without importing each other.
"""

from __future__ import annotations

from django.db.models import F

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


def add_strike(member_email: str, count: int = 1) -> None:
    if not member_email:
        return
    TeamMember.objects.filter(email=member_email.strip().lower()).update(
        strikes=F("strikes") + count
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
    if task.at_event and task.event_start and task.event_end:
        calendar.create_hold(
            email=task.email,
            title=f"{task.ref_code} {task.task} — {task.event_name}",
            start=task.event_start,
            end=task.event_end,
            description=task.venue or "",
        )
    elif task.deadline:
        calendar.create_reminder(
            email=task.email,
            title=f"{task.ref_code} {task.task} due — {task.event_name}",
            due=task.deadline,
        )

    body = f"You've been assigned as {task.task} for {task.event_name} ({task.ref_code}).\n"
    if task.deadline:
        body += f"Deadline: {task.deadline:%d %b %Y, %H:%M}\n"
    email_service.send(
        task.email,
        f"[Assigned] {task.ref_code} {task.task} — {task.event_name}",
        body,
    )
