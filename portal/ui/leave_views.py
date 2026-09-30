"""
Out-of-work requests: a member asks from their Profile, the POC/Admin decides on
the Approvals page. The rules live in `engine/leave.py`; these check who is
asking and turn its `LeaveError`s into messages.
"""

from __future__ import annotations

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_POST

from core.decorators import secretary_or_admin_required, team_required
from core.models import LeaveRequest
from engine import leave as engine

from .forms import LeaveDecisionForm, LeaveRequestForm


@team_required
@require_POST
def leave_request(request):
    member = request.roles.member
    form = LeaveRequestForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Give a reason and both dates.")
        return redirect("profile")
    try:
        engine.request_leave(
            member,
            form.cleaned_data["reason"],
            form.cleaned_data["start_date"],
            form.cleaned_data["end_date"],
        )
    except engine.LeaveError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request, "Request sent to the POC. Nothing changes until it's approved."
        )
    return redirect("profile")


@team_required
@require_POST
def leave_cancel(request, pk):
    # Scoped to the caller's own requests: someone else's pk is simply a 404.
    leave = get_object_or_404(LeaveRequest, pk=pk, member=request.roles.member)
    try:
        engine.cancel_leave(leave, request.roles.email)
    except engine.LeaveError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Request withdrawn.")
    return redirect("profile")


@team_required
@require_POST
def leave_return(request):
    try:
        engine.return_now(request.roles.member, request.roles.email)
    except engine.LeaveError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Welcome back — you're on work again.")
    return redirect("profile")


@secretary_or_admin_required
@require_POST
def leave_decide(request, pk):
    leave = get_object_or_404(LeaveRequest, pk=pk)
    form = LeaveDecisionForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Choose Approve or Decline.")
        return redirect("approval-list")
    approve = form.cleaned_data["decision"] == "approve"
    try:
        decided = engine.decide_leave(
            leave, approve=approve, actor=request.roles.email, note=form.cleaned_data["note"]
        )
    except engine.LeaveError as exc:
        messages.error(request, str(exc))
    else:
        note = f"{decided.member.name}'s request was {'approved' if approve else 'declined'}."
        open_tasks = engine.open_tasks_for(decided.member) if approve else []
        if open_tasks:
            note += (
                f" They still hold {len(open_tasks)} open task(s) — reassign any that fall in these "
                "dates from the Assignments page."
            )
        messages.success(request, note)
    return redirect("approval-list")
