"""
Confirming a request (docs/PRD.md §5.3).

Ported from `server/src/engine/confirm.ts`. Shared by two paths: auto-acceptance
of an ungated request, and a secretary approving a gated one.

For every filled, not-yet-confirmed task: mark it CONFIRMED, credit its points
to the assignee, and add it to the roster if it's a contact-facing role. Then
write the roster onto the request, move it to 'Request Accepted', and send the
invitations.

**Idempotency matters here.** Tasks already CONFIRMED or DONE are skipped, so
approving twice — or a retry after a partial failure — can never award the same
points a second time.
"""

from __future__ import annotations

from django.db import transaction

from core.activity import log_activity
from core.constants import ROSTER_ROLES, RequestStatus, TaskStatus
from services import email as email_service

from .notify import award_points, notify_assignee


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
            detail=f"{task.task} confirmed (+{task.points or 0} pts)",
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
    points_by_email: dict[str, int] = {}

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

        # Already handled on an earlier run — don't re-award or re-notify.
        if task.status in (TaskStatus.CONFIRMED, TaskStatus.DONE):
            continue

        task.status = TaskStatus.CONFIRMED
        task.points_awarded = True
        task.save(update_fields=["status", "points_awarded"])

        key = task.email.lower()
        points_by_email[key] = points_by_email.get(key, 0) + (task.points or 0)
        newly_confirmed.append(task)

    for member_email, points in points_by_email.items():
        award_points(member_email, points)

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
