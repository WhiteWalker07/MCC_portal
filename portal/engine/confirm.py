"""
Confirming a request (docs/PRD.md §5.3).

Ported from `server/src/engine/confirm.ts`, since adapted: points are now
credited on task *completion*, not here (see engine/workflow.py's
`_award_completion_points`) — confirming only locks a task in as CONFIRMED and
adds it to the roster if it's a contact-facing role. Then the roster is written
onto the request, it moves to 'Request Accepted', and the acceptance email goes
to the requesting club with the roster CC'd -- so the club and the people
covering their event are on the same thread from the start.

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
    newly_confirmed, roster, already_accepted = _commit_confirmation(request_obj)

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

    if already_accepted:
        # A retry or a double-submitted approval landed here after an earlier
        # call already accepted this request — the club-facing email and the
        # "accepted" log entry must fire exactly once, so there's nothing left
        # to do.
        return

    email_service.send(
        request_obj.contact_email,
        f"[Accepted] {request_obj.ref_code} — {request_obj.event_name}",
        _roster_email(request_obj, roster),
        cc=[entry["email"] for entry in roster if entry.get("email")],
        message_id=email_service.thread_id_for(request_obj.ref_code),
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
    # Lock this row and re-check its status before touching it — two
    # concurrent calls for the same request (a double-submitted approval, or
    # a retry racing a still-in-flight first attempt) must not both see "not
    # yet accepted". This reads the lock through a fresh query rather than
    # replacing `request_obj` itself, since callers keep using the same
    # instance afterward (e.g. complete_task via task.request's cached FK).
    locked_status = (
        type(request_obj)
        .objects.select_for_update()
        .values_list("status", flat=True)
        .get(pk=request_obj.pk)
    )
    already_accepted = locked_status in RequestStatus.CONFIRMED_STATES

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

    if not already_accepted:
        request_obj.status = RequestStatus.ACCEPTED
        request_obj.roster = roster
        request_obj.save(update_fields=["status", "roster"])

    return newly_confirmed, roster, already_accepted


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
