"""
Confirming a request (docs/PRD.md §5.3).

Ported from `server/src/engine/confirm.ts`, since adapted: points are now
credited on task *completion*, not here (see engine/workflow.py's
`_award_completion_points`) — confirming only locks a task in as CONFIRMED and
adds it to the roster if it's a contact-facing role. Then the roster is written
onto the request, it moves to 'Request Accepted', and the invitations go out.

**Idempotency matters here.** Tasks already CONFIRMED or DONE are skipped, so
approving twice — or a retry after a partial failure — can never re-notify or
re-add the same roster entry twice.
"""

from __future__ import annotations

from django.db import transaction

from core.activity import log_activity
from core.constants import ROSTER_ROLES, RequestStatus, TaskStatus
from services import email as email_service

from .notify import notify_assignee


def confirm_request(request_obj) -> None:
    newly_confirmed, roster = _commit_confirmation(request_obj)

    # Side effects run only after the state change is committed, so a slow or
    # failing mail relay can't leave the database half-updated.
    for task in newly_confirmed:
        notify_assignee(task)
        log_activity(
            "confirmed",
            request_obj=request_obj,
            ref_code=task.ref_code,
            member=task.email,
            detail=f"{task.task} confirmed ({task.points or 0} pts on completion)",
        )

    email_service.send(
        request_obj.contact_email,
        f"[Accepted] {request_obj.ref_code} — {request_obj.event_name}",
        _roster_email(request_obj, roster),
    )
    log_activity(
        "accepted",
        request_obj=request_obj,
        actor="engine",
        detail=f"Request Accepted; {len(roster)} contact(s) in roster",
    )


@transaction.atomic
def _commit_confirmation(request_obj):
    """Everything that touches the database, in one transaction."""
    roster: list[dict] = []
    newly_confirmed = []

    for task in request_obj.tasks.select_for_update():
        if task.status == TaskStatus.UNFILLED or not task.email:
            continue

        if task.task in ROSTER_ROLES:
            roster.append(
                {
                    "role": task.task,
                    "name": task.member,
                    "email": task.email,
                    "phone": task.phone or "",
                }
            )

        # Already handled on an earlier run — don't re-notify.
        if task.status in (TaskStatus.CONFIRMED, TaskStatus.DONE):
            continue

        task.status = TaskStatus.CONFIRMED
        task.save(update_fields=["status"])
        newly_confirmed.append(task)

    request_obj.status = RequestStatus.ACCEPTED
    request_obj.roster = roster
    request_obj.save(update_fields=["status", "roster"])

    return newly_confirmed, roster


def _roster_email(request_obj, roster: list[dict]) -> str:
    lines = [
        f"  {entry['role']}: {entry['name']} <{entry['email']}>"
        + (f" · {entry['phone']}" if entry.get("phone") else "")
        for entry in roster
    ]
    return (
        f"Your request {request_obj.ref_code} ({request_obj.event_name}) has been accepted.\n\n"
        f"Assigned team:\n" + ("\n".join(lines) or "  (none)") + "\n"
    )
