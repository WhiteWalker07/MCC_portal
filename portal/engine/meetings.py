"""
Team meetings: who can be called, who takes the minutes, what the invitees are
told, and how attendance is marked.

Kept out of the views so the rules are testable without a request/response
cycle, in the same spirit as `event_changes.py`.

**Who can be invited.** Any active team member who isn't marked "Out of work".
That is one rule, applied in one place (`invitable_members`), so the picker, the
"whole team" and "by vertical" shortcuts and the server-side check on a
hand-picked list can't disagree.

**MOM + venue.** The caller may ask for one person to take the minutes and book
the venue. The system suggests the first-year invitee with the fewest points
(the same fairness order task assignment uses); the caller can pick another
first-year invitee. It is a responsibility only: nothing is uploaded and there
are no points.

**Attendance.** Marking someone Absent gives them a yellow strike, automatically;
changing that mark to anything else takes the strike back off. `strike_given`
on the invite is what makes both directions idempotent -- re-saving the same
mark never double-strikes, and only a strike this meeting gave is ever removed.

**Calendar holds cannot be moved or deleted** (services/calendar.py stores no
event id), so an edit or a cancellation says plainly that any earlier entry on
the invitee's calendar is stale, exactly as a change of event time does.
"""

from __future__ import annotations

from datetime import datetime

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.activity import log_activity
from core.constants import Attendance, Availability
from core.models import Meeting, MeetingInvite, TeamMember
from services import email as email_service
from services.calendar import calendar_service

from .assign import _fairness_key

INVITE_ALL = "all"
INVITE_VERTICALS = "verticals"
INVITE_PEOPLE = "people"
INVITE_MODES = [
    (INVITE_ALL, "The whole team"),
    (INVITE_VERTICALS, "Chosen verticals"),
    (INVITE_PEOPLE, "Chosen people"),
]


class MeetingError(ValueError):
    """A meeting change that the rules don't allow; the message is shown to the user."""


def _fmt(value: datetime) -> str:
    return timezone.localtime(value).strftime("%a %d %b %Y, %H:%M")


def _when(meeting) -> str:
    return f"{_fmt(meeting.start)} – {timezone.localtime(meeting.end):%H:%M}"


# ── Who can be called ───────────────────────────────────────────────────────


def invitable_members():
    """Active members who are on work. Someone marked Out of work can't be called."""
    return TeamMember.objects.filter(active=True).exclude(availability=Availability.OUT)


def resolve_invitees(mode: str, verticals=(), emails=()) -> list[TeamMember]:
    """
    The people a meeting is being called for. `mode` is one of the `INVITE_*`
    constants: everyone invitable, invitable members whose primary *or* secondary
    vertical is one of `verticals`, or the invitable members among `emails`.
    Anyone out of work is dropped whatever the mode.
    """
    pool = invitable_members()
    if mode == INVITE_VERTICALS:
        wanted = set(verticals)
        return [m for m in pool if m.vertical in wanted or m.secondary_vertical in wanted]
    if mode == INVITE_PEOPLE:
        chosen = {str(e).strip().lower() for e in emails if str(e).strip()}
        return [m for m in pool if m.email in chosen]
    return list(pool)


def choose_mom(invitees, *, exclude_email: str = "") -> TeamMember | None:
    """
    The suggested minutes-taker: the first-year invitee with the fewest points
    (name breaks a tie). Second-years only supervise, so they aren't picked.
    """
    pool = [m for m in invitees if m.year != 2 and m.email != (exclude_email or "").lower()]
    return min(pool, key=_fairness_key) if pool else None


def _check_mom(member, invitees) -> None:
    if member is None:
        return
    if member.email not in {m.email for m in invitees}:
        raise MeetingError("The person taking the minutes has to be one of the people invited.")
    if member.year == 2:
        raise MeetingError("Second-years only supervise — pick a first-year to take the minutes.")


# ── Emails ──────────────────────────────────────────────────────────────────


def _thread(meeting) -> str:
    return email_service.thread_id_for(meeting.thread_key)


def _venue_line(meeting, mom) -> str:
    if meeting.venue:
        return f"Venue: {meeting.venue}"
    if mom is not None:
        return f"Venue: to be confirmed — {mom.name} is booking it"
    return "Venue: to be confirmed"


def _details(meeting, mom, caller_name: str) -> str:
    lines = [
        f"When: {_when(meeting)}",
        _venue_line(meeting, mom),
        f"Called by: {caller_name}",
    ]
    if mom is not None:
        lines.append(f"Minutes (MOM) and venue booking: {mom.name}")
    text = "\n".join(lines)
    if meeting.agenda.strip():
        text += f"\n\nAgenda:\n{meeting.agenda.strip()}"
    return text


def _caller_name(meeting) -> str:
    member = TeamMember.objects.filter(email=(meeting.called_by or "").lower()).first()
    return member.name if member else meeting.called_by


def _mom_member(meeting) -> TeamMember | None:
    if not meeting.mom_email:
        return None
    return TeamMember.objects.filter(email=meeting.mom_email.lower()).first()


def _hold(meeting, member) -> None:
    calendar_service().create_hold(
        email=member.email,
        title=f"Team meeting: {meeting.title}",
        start=meeting.start,
        end=meeting.end,
        description="\n".join(x for x in (meeting.venue, meeting.agenda.strip()) if x),
    )


def _send_mom_duty(meeting, mom) -> None:
    caller = _caller_name(meeting)
    booking = (
        f"make sure {meeting.venue} is booked for the time above (check with {caller} if you're unsure)"
        if meeting.venue
        else "book a room for the time above and tell the team which one"
    )
    email_service.send(
        mom.email,
        f"[MOM duty] {meeting.title}",
        f"You have been asked to take the minutes (MOM) and book the venue for "
        f"\"{meeting.title}\".\n\n"
        f"{_details(meeting, mom, caller)}\n\n"
        f"That means: {booking}, and write up the minutes and share them with the team afterwards. "
        "It is a responsibility only: there are no points for it. If you can't do it, tell "
        f"{caller} straight away so it can be given to someone else.",
        in_reply_to=_thread(meeting),
    )


# ── Create / change / cancel ────────────────────────────────────────────────


def create_meeting(
    *,
    title: str,
    start,
    end,
    venue: str,
    agenda: str,
    called_by: str,
    invitees,
    wants_mom: bool,
    mom: TeamMember | None = None,
) -> Meeting:
    """
    Save the meeting and its invitees, then tell everyone. The caller has already
    checked permission and that `invitees` are all invitable; nothing here trusts
    a bare list of people, so it is checked once more.
    """
    invitees = list(invitees)
    if not invitees:
        raise MeetingError("Nobody is invited — pick at least one person.")
    _reject_unavailable(invitees)
    if end <= start:
        raise MeetingError("The meeting must end after it starts.")

    if wants_mom:
        mom = mom or choose_mom(invitees)
        if mom is None:
            raise MeetingError("Nobody invited can take the minutes — invite at least one first-year.")
        _check_mom(mom, invitees)
    else:
        mom = None

    with transaction.atomic():
        meeting = Meeting.objects.create(
            title=title.strip(),
            start=start,
            end=end,
            venue=(venue or "").strip(),
            agenda=(agenda or "").strip(),
            called_by=called_by.strip().lower(),
            wants_mom=bool(wants_mom),
            mom_email=mom.email if mom else "",
        )
        MeetingInvite.objects.bulk_create(MeetingInvite(meeting=meeting, member=m) for m in invitees)

    email_service.send(
        [m.email for m in invitees],
        f"[Meeting] {meeting.title} — {_fmt(meeting.start)}",
        f"You are invited to a team meeting.\n\n"
        f"{meeting.title}\n{_details(meeting, mom, _caller_name(meeting))}\n\n"
        "If you can't attend, tell the person who called it before the meeting. Attendance is "
        "taken, and an unexcused absence is recorded as a yellow strike.",
        message_id=_thread(meeting),
    )
    if mom is not None:
        _send_mom_duty(meeting, mom)
    for member in invitees:
        _hold(meeting, member)

    log_activity(
        "meeting-called",
        actor=meeting.called_by,
        detail=f"{meeting.title}, {_when(meeting)}: {len(invitees)} invited"
        + (f", MOM {mom.name}" if mom else ""),
    )
    return meeting


def _reject_unavailable(members) -> None:
    allowed = {m.email for m in invitable_members().filter(email__in=[m.email for m in members])}
    missing = [m.name for m in members if m.email not in allowed]
    if missing:
        raise MeetingError(
            "These people can't be called (out of work or no longer on the team): " + ", ".join(missing)
        )


def _require_open(meeting) -> None:
    if meeting.is_cancelled:
        raise MeetingError("This meeting has been cancelled.")
    if meeting.has_started:
        raise MeetingError("This meeting has already started — it can no longer be changed.")


def update_meeting(
    meeting,
    *,
    title: str,
    start,
    end,
    venue: str,
    agenda: str,
    invitees,
    wants_mom: bool,
    mom: TeamMember | None = None,
    actor: str,
) -> None:
    """
    Change a meeting that hasn't started, and tell everyone affected: people who
    stay are told what changed, people who were added get the full invitation,
    people who were dropped are told they're no longer needed, and a changed
    minutes-taker is told (and the previous one released).
    """
    _require_open(meeting)
    invitees = list(invitees)
    if not invitees:
        raise MeetingError("Nobody is invited — pick at least one person.")
    if end <= start:
        raise MeetingError("The meeting must end after it starts.")

    current = {inv.member.email: inv.member for inv in meeting.invites.select_related("member")}
    new = {m.email: m for m in invitees}
    added = [m for e, m in new.items() if e not in current]
    _reject_unavailable(added)  # someone already invited who has since gone out of work stays on the list
    removed = [m for e, m in current.items() if e not in new]
    staying = [m for e, m in new.items() if e in current]

    old_mom = _mom_member(meeting)
    if wants_mom:
        # Keep the current minutes-taker unless they were dropped or a new one was named.
        if mom is None and old_mom is not None and old_mom.email in new:
            mom = old_mom
        mom = mom or choose_mom(invitees)
        if mom is None:
            raise MeetingError("Nobody invited can take the minutes — invite at least one first-year.")
        _check_mom(mom, invitees)
    else:
        mom = None

    changes = []
    if title.strip() != meeting.title:
        changes.append(f"Title: {meeting.title} -> {title.strip()}")
    if (start, end) != (meeting.start, meeting.end):
        changes.append(f"Time: {_when(meeting)} -> {_fmt(start)} – {timezone.localtime(end):%H:%M}")
    if (venue or "").strip() != meeting.venue:
        changes.append(f"Venue: {meeting.venue or 'none'} -> {(venue or '').strip() or 'to be confirmed'}")
    if (agenda or "").strip() != meeting.agenda:
        changes.append("The agenda was updated")
    time_moved = (start, end) != (meeting.start, meeting.end)

    with transaction.atomic():
        meeting.title = title.strip()
        meeting.start, meeting.end = start, end
        meeting.venue = (venue or "").strip()
        meeting.agenda = (agenda or "").strip()
        meeting.wants_mom = bool(wants_mom)
        meeting.mom_email = mom.email if mom else ""
        meeting.save()
        meeting.invites.filter(member__in=removed).delete()
        MeetingInvite.objects.bulk_create(MeetingInvite(meeting=meeting, member=m) for m in added)

    thread = _thread(meeting)
    caller = _caller_name(meeting)
    body_changes = ("What changed:\n  " + "\n  ".join(changes) + "\n\n") if changes else ""
    stale = (
        "If this meeting is on your calendar, please update it: the portal can't edit an entry it "
        "already made, so it may still show the old time.\n"
        if time_moved
        else ""
    )
    if staying or added:
        email_service.send(
            [m.email for m in staying + added],
            f"[Meeting updated] {meeting.title} — {_fmt(meeting.start)}",
            f"A team meeting was changed by {caller}.\n\n{body_changes}"
            f"{meeting.title}\n{_details(meeting, mom, caller)}\n\n{stale}",
            in_reply_to=thread,
        )
    if removed:
        email_service.send(
            [m.email for m in removed],
            f"[Meeting updated] {meeting.title} — you are no longer invited",
            f"{caller} has changed who is invited to \"{meeting.title}\" ({_when(meeting)}). "
            "You are no longer on the list, so you don't need to attend.",
            in_reply_to=thread,
        )
    _announce_mom_change(meeting, old_mom, mom, thread, caller)
    for member in added if not time_moved else added + staying:
        _hold(meeting, member)

    log_activity(
        "meeting-updated",
        actor=actor,
        detail=f"{meeting.title}: " + ("; ".join(changes) or "invitees changed")
        + (f"; +{len(added)}/-{len(removed)} invited" if added or removed else ""),
    )


def _announce_mom_change(meeting, old_mom, new_mom, thread: str, caller: str) -> None:
    if (old_mom.email if old_mom else "") == (new_mom.email if new_mom else ""):
        return
    if new_mom is not None:
        _send_mom_duty(meeting, new_mom)
    if old_mom is not None:
        email_service.send(
            old_mom.email,
            f"[MOM duty] {meeting.title} — released",
            f"You are no longer responsible for the minutes and venue booking for "
            f"\"{meeting.title}\"" + (f"; {new_mom.name} has taken over." if new_mom else "."),
            in_reply_to=thread,
        )


def set_mom(meeting, member: TeamMember | None, *, actor: str) -> None:
    """
    Name (or, with `None`, clear) the minutes-taker without touching anything else.
    `member` must be a first-year who is invited.
    """
    _require_open(meeting)
    invitees = [inv.member for inv in meeting.invites.select_related("member")]
    _check_mom(member, invitees)
    old_mom = _mom_member(meeting)

    meeting.wants_mom = member is not None
    meeting.mom_email = member.email if member else ""
    meeting.save(update_fields=["wants_mom", "mom_email", "updated_at"])

    _announce_mom_change(meeting, old_mom, member, _thread(meeting), _caller_name(meeting))
    log_activity(
        "meeting-mom",
        actor=actor,
        member=member.email if member else "",
        detail=f"{meeting.title}: minutes/venue -> {member.name if member else 'nobody'}",
    )


def cancel_meeting(meeting, *, actor: str) -> None:
    """Cancel a meeting that hasn't started, and tell every invitee."""
    _require_open(meeting)
    meeting.cancelled_at = timezone.now()
    meeting.save(update_fields=["cancelled_at", "updated_at"])

    invitees = [inv.member for inv in meeting.invites.select_related("member")]
    email_service.send(
        [m.email for m in invitees],
        f"[Meeting cancelled] {meeting.title} — {_fmt(meeting.start)}",
        f"The team meeting \"{meeting.title}\" ({_when(meeting)}) has been cancelled by "
        f"{_caller_name(meeting)}. You don't need to attend.\n\n"
        "If it is on your calendar, please delete the entry: the portal can't remove one it "
        "already made.",
        in_reply_to=_thread(meeting),
    )
    log_activity("meeting-cancelled", actor=actor, detail=f"{meeting.title}, {_when(meeting)}")


# ── Attendance ──────────────────────────────────────────────────────────────


def mark_attendance(invite, status: str, *, actor: str) -> MeetingInvite:
    """
    Record `status` for one invitee. Absent gives a yellow strike (once); moving
    away from Absent takes back the strike this meeting gave. The meeting must
    have started and not be cancelled — the caller checked permission.
    """
    if status not in {value for value, _ in Attendance.CHOICES}:
        raise MeetingError("Unknown attendance status.")
    meeting = invite.meeting
    if meeting.is_cancelled:
        raise MeetingError("This meeting was cancelled.")
    if not meeting.has_started:
        raise MeetingError("Attendance can only be marked once the meeting has started.")

    with transaction.atomic():
        locked = MeetingInvite.objects.select_for_update().select_related("member").get(pk=invite.pk)
        gave = took = False
        if status == Attendance.ABSENT and not locked.strike_given:
            TeamMember.objects.filter(pk=locked.member_id).update(yellow_strikes=F("yellow_strikes") + 1)
            locked.strike_given = gave = True
        elif status != Attendance.ABSENT and locked.strike_given:
            # Never below zero: the strike may already have been removed by hand.
            TeamMember.objects.filter(pk=locked.member_id, yellow_strikes__gt=0).update(
                yellow_strikes=F("yellow_strikes") - 1
            )
            locked.strike_given = False
            took = True
        previous = locked.attendance
        locked.attendance = status
        locked.marked_by = actor
        locked.marked_at = timezone.now()
        locked.save(update_fields=["attendance", "strike_given", "marked_by", "marked_at"])

    if status != previous:
        label = dict(Attendance.CHOICES)[status] or "not marked"
        log_activity(
            "attendance",
            actor=actor,
            member=locked.member.email,
            detail=f"{meeting.title}: {label}"
            + ("; yellow strike given" if gave else "")
            + ("; yellow strike removed" if took else ""),
        )
    return locked
