"""
Going Out of work: a member asks (with a reason and a date range), the POC or an
Admin decides, and the member is switched to Out of work on the start date and
back on work after the end date.

Until a request is approved *and* its start date arrives nothing changes for the
member: they keep getting assigned work and can be called to meetings. Their
open tasks are never moved automatically; the POC is shown them when deciding
and reassigns by hand if needed.

`switch_availability` is the one place availability changes, so the banked
on-work/out-of-work day counts and the audit log behave the same whether the
POC flipped the switch by hand, the member came back early, or a leave started
or ended by date.

`run_leave_sweep` is run hourly from the deadline sweep (`manage.py
deadline_check`), so no extra scheduled task is needed. Ending is by the
calendar date in the server's timezone: someone whose leave ends on the 10th is
back at the start of the 11th.
"""

from __future__ import annotations

from datetime import date

from django.db import transaction
from django.utils import timezone

from core.activity import log_activity
from core.config import get_settings
from core.constants import DAY, Availability, LeaveStatus, RequestStatus, TaskStatus
from core.models import LeaveRequest, Task, TeamMember
from services import email as email_service


class LeaveError(ValueError):
    """A leave action the rules don't allow; the message is shown to the user."""


def _fmt(day: date) -> str:
    return day.strftime("%a %d %b %Y")


def _span(leave) -> str:
    if leave.start_date == leave.end_date:
        return _fmt(leave.start_date)
    return f"{_fmt(leave.start_date)} to {_fmt(leave.end_date)}"


# ── The one place availability changes ──────────────────────────────────────


def switch_availability(email: str, next_status: str, actor: str) -> TeamMember:
    """
    Set a member Out of work or back On work, banking the time spent in the state
    they are leaving. Locked read-modify-write: two switches at once would
    otherwise bank the same stretch of time twice.

    Coming back On work also closes any leave that was active, so the Profile
    page never shows someone as on leave while they are working.
    """
    address = (email or "").strip().lower()
    with transaction.atomic():
        member = TeamMember.objects.select_for_update().get(email=address)

        now = timezone.now()
        previous = member.availability if member.availability == Availability.OUT else Availability.AVAILABLE
        changed_at = member.availability_changed_at or now
        segment_days = max(0.0, (now - changed_at).total_seconds() / DAY)

        if previous == Availability.OUT:
            member.out_days = round((member.out_days or 0) + segment_days, 1)
        else:
            member.on_work_days = round((member.on_work_days or 0) + segment_days, 1)

        member.availability = next_status
        member.availability_changed_at = now
        member.save(update_fields=["availability", "availability_changed_at", "on_work_days", "out_days"])

        if next_status == Availability.AVAILABLE:
            LeaveRequest.objects.filter(
                member=member, status=LeaveStatus.APPROVED, started_at__isnull=False, ended_at__isnull=True
            ).update(status=LeaveStatus.ENDED, ended_at=now)

    log_activity("availability", actor=actor, member=address, detail=f"{previous} -> {next_status}")
    return member


# ── Requesting ──────────────────────────────────────────────────────────────


def open_tasks_for(member) -> list[Task]:
    """Tasks the member still has to do on live requests — what the POC should look at."""
    return list(
        Task.objects.filter(email=member.email)
        .exclude(status__in=[TaskStatus.DONE, TaskStatus.UNFILLED])
        .exclude(request__status__in=RequestStatus.TERMINAL)
        .select_related("request")
        .order_by("deadline", "task")
    )


def _task_lines(tasks) -> str:
    if not tasks:
        return "  none"
    return "\n".join(
        f"  {t.ref_code} {t.task}" + (f" (due {timezone.localtime(t.deadline):%d %b, %H:%M})" if t.deadline else "")
        for t in tasks
    )


def open_leave_for(member) -> LeaveRequest | None:
    return member.leave_requests.filter(status__in=LeaveStatus.OPEN).first()


def request_leave(member, reason: str, start_date: date, end_date: date) -> LeaveRequest:
    """
    File a request and tell the POC. A member may have only one open request at a
    time, and can't ask while already marked Out of work (there is nothing to
    approve; they can use "I'm back" when they return).
    """
    reason = (reason or "").strip()
    if not reason:
        raise LeaveError("Give a reason so the POC can decide.")
    today = timezone.localdate()
    if start_date < today:
        raise LeaveError("The start date can't be in the past.")
    if end_date < start_date:
        raise LeaveError("The end date can't be before the start date.")
    # Judge by what the database says now, not by a copy loaded earlier in the request.
    member.refresh_from_db(fields=["availability"])
    if member.availability == Availability.OUT:
        raise LeaveError("You're already marked Out of work.")
    if open_leave_for(member) is not None:
        raise LeaveError("You already have an Out-of-work request open — withdraw it first to send a new one.")

    with transaction.atomic():
        leave = LeaveRequest.objects.create(
            member=member, reason=reason, start_date=start_date, end_date=end_date
        )

    tasks = open_tasks_for(member)
    email_service.send(
        get_settings().secretary_emails,
        f"[Out of work request] {member.name} — {_span(leave)}",
        f"{member.name} <{member.email}> has asked to be marked Out of work.\n\n"
        f"Dates: {_span(leave)}\n"
        f"Reason: {leave.reason}\n\n"
        f"Tasks they still have open ({len(tasks)}):\n{_task_lines(tasks)}\n\n"
        "Approve or decline it from the Approvals page. Nothing changes for them until you approve, "
        "and their tasks are never moved automatically: if you approve, reassign any that fall in "
        "these dates from the Assignments page.",
    )
    log_activity(
        "leave-requested", actor=member.email, member=member.email, detail=f"{_span(leave)}: {leave.reason}"
    )
    return leave


def cancel_leave(leave, actor: str) -> None:
    """Withdraw a request that is still waiting, or an approved one that hasn't started."""
    with transaction.atomic():
        locked = LeaveRequest.objects.select_for_update().get(pk=leave.pk)
        pending = locked.status == LeaveStatus.PENDING
        not_started = locked.status == LeaveStatus.APPROVED and locked.started_at is None
        if not (pending or not_started):
            raise LeaveError("That request can't be withdrawn any more — use \"I'm back\" if you're out.")
        locked.status = LeaveStatus.CANCELLED
        locked.ended_at = timezone.now()
        locked.save(update_fields=["status", "ended_at"])
    log_activity("leave-cancelled", actor=actor, member=leave.member.email, detail=_span(leave))


# ── Deciding ────────────────────────────────────────────────────────────────


def decide_leave(leave, *, approve: bool, actor: str, note: str = "") -> LeaveRequest:
    """
    Approve or decline a waiting request and tell the member. Approving one whose
    start date has arrived switches them out straight away; otherwise the hourly
    sweep does it on the day.
    """
    note = (note or "").strip()
    with transaction.atomic():
        locked = LeaveRequest.objects.select_for_update().select_related("member").get(pk=leave.pk)
        if locked.status != LeaveStatus.PENDING:
            raise LeaveError("This request has already been decided or withdrawn.")
        today = timezone.localdate()
        if approve and locked.end_date < today:
            raise LeaveError(
                "These dates have already passed — decline it and ask them to send a new request."
            )
        locked.status = LeaveStatus.APPROVED if approve else LeaveStatus.REJECTED
        locked.decided_by = actor
        locked.decided_at = timezone.now()
        locked.decision_note = note
        locked.save(update_fields=["status", "decided_by", "decided_at", "decision_note"])
        if approve and locked.start_date <= today:
            _start(locked, actor)

    member = locked.member
    if approve:
        when = (
            "You are now marked Out of work."
            if locked.started_at
            else f"You will be marked Out of work from {_fmt(locked.start_date)}."
        )
        body = (
            f"Your Out-of-work request ({_span(locked)}) was approved. {when} You'll be back on work "
            f"automatically after {_fmt(locked.end_date)}, or you can press \"I'm back\" on your Profile "
            "page sooner. While you are out you won't get new assignments or meeting invitations.\n"
            "If you still hold tasks in these dates, tell the POC so they can be reassigned."
        )
        subject = f"[Out of work approved] {_span(locked)}"
    else:
        body = f"Your Out-of-work request ({_span(locked)}) was declined."
        subject = f"[Out of work declined] {_span(locked)}"
    if note:
        body += f"\n\nNote from the POC: {note}"
    email_service.send(member.email, subject, body)

    log_activity(
        "leave-approved" if approve else "leave-declined",
        actor=actor,
        member=member.email,
        detail=f"{_span(locked)}" + (f": {note}" if note else ""),
    )
    return locked


# ── Starting and ending ─────────────────────────────────────────────────────


def _start(leave, actor: str) -> None:
    if leave.member.availability != Availability.OUT:
        switch_availability(leave.member.email, Availability.OUT, actor)
    leave.started_at = timezone.now()
    leave.save(update_fields=["started_at"])


def return_now(member, actor: str) -> None:
    """The member is back early (or an admin marked them out and they are back)."""
    if member.availability != Availability.OUT:
        raise LeaveError("You're already on work.")
    switch_availability(member.email, Availability.AVAILABLE, actor)


def run_leave_sweep() -> dict:
    """
    Start approved leaves whose day has come and end those whose last day has
    passed. Idempotent, so an overlapping run or a missed hour can't double
    anything. Returns `{"started": n, "ended": n}`.
    """
    today = timezone.localdate()
    started = ended = 0

    for leave in LeaveRequest.objects.filter(
        status=LeaveStatus.APPROVED, started_at__isnull=True, start_date__lte=today
    ).select_related("member"):
        with transaction.atomic():
            locked = LeaveRequest.objects.select_for_update().select_related("member").get(pk=leave.pk)
            if locked.status != LeaveStatus.APPROVED or locked.started_at is not None:
                continue
            if locked.end_date < today:
                # Never got the chance to start (the sweep was down for days): it simply lapses.
                locked.status = LeaveStatus.ENDED
                locked.ended_at = timezone.now()
                locked.save(update_fields=["status", "ended_at"])
                continue
            _start(locked, "engine")
            started += 1
        log_activity("leave-started", actor="engine", member=leave.member.email, detail=_span(leave))

    for leave in LeaveRequest.objects.filter(
        status=LeaveStatus.APPROVED, started_at__isnull=False, end_date__lt=today
    ).select_related("member"):
        with transaction.atomic():
            locked = LeaveRequest.objects.select_for_update().select_related("member").get(pk=leave.pk)
            if locked.status != LeaveStatus.APPROVED or locked.ended_at is not None:
                continue
            if locked.member.availability == Availability.OUT:
                # Also closes this leave (see switch_availability).
                switch_availability(locked.member.email, Availability.AVAILABLE, "engine")
            LeaveRequest.objects.filter(pk=locked.pk, status=LeaveStatus.APPROVED).update(
                status=LeaveStatus.ENDED, ended_at=timezone.now()
            )
            ended += 1
        log_activity("leave-ended", actor="engine", member=leave.member.email, detail=_span(leave))

    return {"started": started, "ended": ended}
