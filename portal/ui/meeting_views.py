"""
Team-meeting views: list, call, edit, cancel, change the minutes-taker, and mark
attendance. The rules live in `engine/meetings.py`; these only check who is
asking, translate forms to and from it, and show its `MeetingError`s.

Anyone on the team sees the meetings they are invited to; whoever called one
(and the POC/Admin) manages it. Only the POC, Admin and vertical heads may call
one at all (`can_call_meeting`).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.constants import Attendance
from core.models import Meeting, TeamMember
from core.roles import can_call_meeting, can_manage_meeting, can_take_attendance
from engine import meetings as engine

from .forms import MeetingForm, MomForm


def _readable(roles, meeting) -> bool:
    """The caller, the POC/Admin, or anyone who was invited."""
    if can_manage_meeting(roles, meeting):
        return True
    return meeting.invites.filter(member__email=roles.email).exists()


def _visible_meetings(roles):
    qs = Meeting.objects.all()
    if roles.is_staff_side:
        return qs
    return qs.filter(Q(called_by=roles.email) | Q(invites__member__email=roles.email)).distinct()


def _mom_candidates(members):
    return [m for m in members if m.year != 2]


@login_required
def meeting_list(request):
    roles = request.roles
    now = timezone.now()
    visible = _visible_meetings(roles).prefetch_related("invites")
    upcoming = sorted((m for m in visible if m.end >= now and not m.is_cancelled), key=lambda m: m.start)
    past = [m for m in visible if m.end < now or m.is_cancelled]
    return render(
        request,
        "ui/meeting_list.html",
        {"upcoming": upcoming, "past": past, "can_call": can_call_meeting(roles)},
    )


def _invitees_from(form) -> list[TeamMember]:
    return engine.resolve_invitees(
        form.cleaned_data["invite_mode"],
        form.cleaned_data.get("verticals") or (),
        form.cleaned_data.get("people") or (),
    )


def _mom_from(form, invitees):
    address = form.cleaned_data.get("mom_email") or ""
    if not address:
        return None
    return next((m for m in invitees if m.email == address), None) or TeamMember.objects.filter(email=address).first()


@login_required
def meeting_new(request):
    roles = request.roles
    if not can_call_meeting(roles):
        raise PermissionDenied("Only the POC, an admin or a vertical head can call a meeting.")

    members = list(engine.invitable_members())
    form = MeetingForm(
        request.POST or None, members=members, mom_candidates=_mom_candidates(members), require_future=True
    )
    if request.method == "POST" and form.is_valid():
        invitees = _invitees_from(form)
        try:
            meeting = engine.create_meeting(
                title=form.cleaned_data["title"],
                start=form.cleaned_data["start"],
                end=form.cleaned_data["end"],
                venue=form.cleaned_data["venue"],
                agenda=form.cleaned_data["agenda"],
                called_by=roles.email,
                invitees=invitees,
                wants_mom=form.cleaned_data["wants_mom"],
                mom=_mom_from(form, invitees),
            )
        except engine.MeetingError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, f"Meeting called — {len(invitees)} people have been emailed.")
            return redirect("meeting-detail", pk=meeting.pk)
    return render(request, "ui/meeting_form.html", {"form": form, "editing": None})


@login_required
def meeting_detail(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)
    roles = request.roles
    if not _readable(roles, meeting):
        raise PermissionDenied("You weren't invited to this meeting.")

    invites = list(meeting.invites.select_related("member"))
    manage = can_manage_meeting(roles, meeting)
    mom = TeamMember.objects.filter(email=meeting.mom_email).first() if meeting.mom_email else None
    open_for_changes = not meeting.is_cancelled and not meeting.has_started
    mom_form = None
    if manage and open_for_changes:
        mom_form = MomForm(
            initial={"mom_email": meeting.mom_email},
            candidates=_mom_candidates(inv.member for inv in invites),
        )
    return render(
        request,
        "ui/meeting_detail.html",
        {
            "meeting": meeting,
            "invites": invites,
            "mom": mom,
            "caller": TeamMember.objects.filter(email=meeting.called_by).first(),
            "can_manage": manage,
            "can_change": manage and open_for_changes,
            "can_mark": can_take_attendance(roles, meeting),
            "mom_form": mom_form,
            "attendance_choices": Attendance.CHOICES,
            "marked": sum(1 for inv in invites if inv.attendance),
        },
    )


@login_required
def meeting_edit(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)
    roles = request.roles
    if not can_manage_meeting(roles, meeting):
        raise PermissionDenied("Only whoever called the meeting, or the POC/Admin, can change it.")
    if meeting.is_cancelled or meeting.has_started:
        messages.error(request, "A meeting that has started or been cancelled can't be changed.")
        return redirect("meeting-detail", pk=meeting.pk)

    invited = [inv.member for inv in meeting.invites.select_related("member")]
    # Offer everyone invitable plus whoever is already invited, so editing never
    # silently drops somebody just because they've since gone out of work.
    members = list({m.email: m for m in [*engine.invitable_members(), *invited]}.values())
    members.sort(key=lambda m: m.name)

    initial = {
        "title": meeting.title,
        "start": meeting.start,
        "end": meeting.end,
        "venue": meeting.venue,
        "agenda": meeting.agenda,
        "invite_mode": "people",
        "people": [m.email for m in invited],
        "wants_mom": meeting.wants_mom,
        "mom_email": meeting.mom_email,
    }
    form = MeetingForm(
        request.POST or None,
        initial=initial,
        members=members,
        mom_candidates=_mom_candidates(members),
        require_future=True,
    )
    if request.method == "POST" and form.is_valid():
        invitees = _invitees_from(form)
        try:
            engine.update_meeting(
                meeting,
                title=form.cleaned_data["title"],
                start=form.cleaned_data["start"],
                end=form.cleaned_data["end"],
                venue=form.cleaned_data["venue"],
                agenda=form.cleaned_data["agenda"],
                invitees=invitees,
                wants_mom=form.cleaned_data["wants_mom"],
                mom=_mom_from(form, invitees),
                actor=roles.email,
            )
        except engine.MeetingError as exc:
            form.add_error(None, str(exc))
        else:
            messages.success(request, "Meeting updated; the people affected have been emailed.")
            return redirect("meeting-detail", pk=meeting.pk)
    return render(request, "ui/meeting_form.html", {"form": form, "editing": meeting})


@login_required
@require_POST
def meeting_cancel(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)
    if not can_manage_meeting(request.roles, meeting):
        raise PermissionDenied("Only whoever called the meeting, or the POC/Admin, can cancel it.")
    try:
        engine.cancel_meeting(meeting, actor=request.roles.email)
    except engine.MeetingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Meeting cancelled; everyone invited has been told.")
    return redirect("meeting-detail", pk=meeting.pk)


@login_required
@require_POST
def meeting_set_mom(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)
    if not can_manage_meeting(request.roles, meeting):
        raise PermissionDenied("Only whoever called the meeting, or the POC/Admin, can change this.")
    invited = [inv.member for inv in meeting.invites.select_related("member")]
    form = MomForm(request.POST, candidates=_mom_candidates(invited))
    if not form.is_valid():
        messages.error(request, "Pick someone from the invited first-years.")
        return redirect("meeting-detail", pk=meeting.pk)
    address = form.cleaned_data["mom_email"]
    member = next((m for m in invited if m.email == address), None) if address else None
    try:
        engine.set_mom(meeting, member, actor=request.roles.email)
    except engine.MeetingError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(
            request,
            f"{member.name} will take the minutes and book the venue." if member else "Nobody is assigned the minutes now.",
        )
    return redirect("meeting-detail", pk=meeting.pk)


@login_required
@require_POST
def meeting_attendance(request, pk):
    meeting = get_object_or_404(Meeting, pk=pk)
    roles = request.roles
    if not can_manage_meeting(roles, meeting):
        raise PermissionDenied("Only whoever called the meeting, or the POC/Admin, can mark attendance.")
    if not can_take_attendance(roles, meeting):
        messages.error(request, "Attendance can only be marked once the meeting has started.")
        return redirect("meeting-detail", pk=meeting.pk)

    changed = strikes = 0
    try:
        for invite in meeting.invites.select_related("member"):
            status = request.POST.get(f"status_{invite.pk}")
            if status is None or status == invite.attendance:
                continue
            had_strike = invite.strike_given
            updated = engine.mark_attendance(invite, status, actor=roles.email)
            changed += 1
            strikes += int(updated.strike_given and not had_strike)
    except engine.MeetingError as exc:
        messages.error(request, str(exc))
        return redirect("meeting-detail", pk=meeting.pk)

    note = f"Attendance saved ({changed} changed)." if changed else "Nothing changed."
    if strikes:
        note += f" {strikes} yellow strike(s) were given for absences."
    messages.success(request, note)
    return redirect("meeting-detail", pk=meeting.pk)
