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
from core.config import get_settings
from core.constants import (
    ROSTER_ROLES,
    TASK_CONTENT_WRITER,
    TASK_GRAPHIC_DESIGNER,
    RequestStatus,
    RequestType,
    TaskStatus,
)
from core.models import TeamMember
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

    if request_obj.type == RequestType.POST:
        _notify_graphic_heads(request_obj)


def _notify_graphic_heads(request_obj) -> None:
    """
    Tell the Graphic Designs head(s) which Graphic Designer (and Content Writer)
    the engine picked for a just-approved Post, and that they can change it from
    Assignments — they, not the POC, own that choice now.

    If nobody could be staffed at all, the Post would otherwise sit in
    'Request Accepted' forever, waiting on tasks nobody holds; in that case the
    POC is told as well.
    """
    tasks = {
        t.task: t
        for t in request_obj.tasks.filter(task__in=[TASK_GRAPHIC_DESIGNER, TASK_CONTENT_WRITER])
    }
    designer = tasks.get(TASK_GRAPHIC_DESIGNER)
    writer = tasks.get(TASK_CONTENT_WRITER)

    def describe(label: str, task) -> str:
        if task is None:
            return f"  {label}: not part of this request's setup"
        if task.email:
            return f"  {label}: {task.member} <{task.email}>"
        return f"  {label}: UNFILLED ({task.reason or 'nobody eligible'})"

    nobody = not any(t is not None and t.email for t in (designer, writer))
    body = (
        f"Post request {request_obj.ref_code} — {request_obj.event_name} has been approved.\n\n"
        f"Assigned automatically:\n{describe('Graphic Designer', designer)}\n"
        f"{describe('Content Writer', writer)}\n\n"
        "You can change the Graphic Designer (or fill it, if it says UNFILLED) from the "
        "Assignments page.\n"
    )
    if nobody:
        body += (
            "\nNobody could be assigned to either role, so this request cannot move on "
            "until someone is assigned from Assignments.\n"
        )

    vertical = (designer.vertical if designer else "") or "Graphic Designs"
    heads = list(
        TeamMember.objects.filter(domain_head_of=vertical, active=True).values_list("email", flat=True)
    )
    poc = list(get_settings().secretary_emails or [])
    # With no head on record, or nobody staffed at all, the POC needs to know.
    recipients = heads + (poc if (nobody or not heads) else [])

    email_service.send(
        recipients,
        f"[Post approved] {request_obj.ref_code} — {request_obj.event_name}",
        body,
    )
    log_activity(
        "graphic-head-notified",
        request_obj=request_obj,
        ref_code=request_obj.ref_code,
        actor="engine",
        detail=f"Told {len(set(recipients))} person(s) who the Graphic Designer is",
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

    if locked_status == RequestStatus.REJECTED:
        # A rejection committed first (two approvers racing): it wins, and the
        # tasks stay unconfirmed, nobody is told, the request stays rejected.
        return [], [], True

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
    # A club that didn't raise the request itself should know why it exists.
    entered_for_you = (
        "The Media Committee entered this request on your behalf, so you don't need to submit it "
        "again. It is already accepted.\n\n"
        if request_obj.created_on_behalf_by
        else ""
    )
    return (
        f"Your request {request_obj.ref_code} ({request_obj.event_name}) has been accepted.\n\n"
        f"{entered_for_you}"
        f"Assigned team:\n" + ("\n".join(lines) or "  (none)") + "\n"
    )
