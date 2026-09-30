"""
Server-side role resolution and assignment authorisation.

Ported from `server/src/engine/serverRoles.ts`. This is the authoritative role
source — the browser is never trusted for it. Roles are resolved from the
database on every request, so granting or revoking access takes effect
immediately and never requires a deploy (docs/PRD.md §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from .constants import (
    EDIT_CUTOFF_HOURS,
    HOUR,
    STRIKE_YELLOW,
    SUBEVENT_CUTOFF_HOURS,
    TASK_SUPERVISOR,
    RequestStatus,
    RequestType,
)
from .models import Committee, PortalSettings, TeamMember


def _lower_all(values) -> set[str]:
    return {str(v).strip().lower() for v in (values or []) if str(v).strip()}


@dataclass(frozen=True)
class PortalRoles:
    """Everything the portal needs to know about who is asking."""

    email: str = ""
    is_authenticated: bool = False
    is_secretary: bool = False
    is_admin: bool = False
    is_team: bool = False
    is_domain_head: bool = False
    domain_head_of: str = ""
    is_second_year: bool = False
    is_coordinator: bool = False
    is_supervisor: bool = False
    member: TeamMember | None = None
    committee: Committee | None = None
    names: list[str] = field(default_factory=list)

    @property
    def is_staff_side(self) -> bool:
        """Secretary or admin — the two roles that can act across the whole portal."""
        return self.is_secretary or self.is_admin

    @property
    def can_reach_assignments(self) -> bool:
        """
        May open the Assignments pages at all. A second-year is *not* let in just
        for being one any more — they only supervise, and a supervisor gets in
        (read-only, for their own requests) because they hold that role.
        """
        return (
            self.is_coordinator
            or self.is_domain_head
            or self.is_supervisor
            or self.is_staff_side
        )


ANONYMOUS = PortalRoles()


def resolve_roles(email: str) -> PortalRoles:
    """Resolve the caller's roles from the database. `email` may be blank."""
    address = (email or "").strip().lower()
    if not address:
        return ANONYMOUS

    settings = PortalSettings.load()
    # A removed (deactivated) member keeps their row for history but not their
    # access: no My Tasks, no assignment/strike rights, no supervisor role.
    row = TeamMember.objects.filter(email=address).first()
    removed = row is not None and not row.active
    member = None if removed else row
    committee = Committee.objects.filter(email=address).first()

    # "Coordinator" isn't a stored flag — you are one if you currently coordinate
    # at least one request. Likewise "supervisor". A removed member is neither,
    # whatever requests still name them, until those are reassigned.
    from .models import Request  # local import: avoids a cycle at module load

    is_coordinator = not removed and Request.objects.filter(coordinator_email=address).exists()
    is_supervisor = not removed and Request.objects.filter(supervisor_email=address).exists()

    is_secretary = address in _lower_all(settings.secretary_emails)
    is_admin = address in _lower_all(settings.admin_emails)
    domain_head_of = (member.domain_head_of or "") if member else ""

    names: list[str] = []
    if committee:
        names.append("committee")
    if member:
        names.append("team")
    if is_coordinator:
        names.append("coordinator")
    if is_supervisor:
        names.append("supervisor")
    if domain_head_of:
        names.append("domainHead")
    if is_secretary:
        names.append("secretary")
    if is_admin:
        names.append("admin")

    return PortalRoles(
        email=address,
        is_authenticated=True,
        is_secretary=is_secretary,
        is_admin=is_admin,
        is_team=member is not None,
        is_domain_head=bool(domain_head_of),
        domain_head_of=domain_head_of,
        is_second_year=bool(member and member.year == 2),
        is_coordinator=is_coordinator,
        is_supervisor=is_supervisor,
        member=member,
        committee=committee,
        names=names,
    )


def can_assign(
    roles: PortalRoles, task_vertical: str, coordinator_email: str, task_name: str = ""
) -> bool:
    """
    May this caller assign or modify a task in `task_vertical` on a request
    coordinated by `coordinator_email`?

    Secretaries and admins may act anywhere. A domain head may act within their
    own vertical. An event coordinator may act on the events they coordinate.
    Being a second-year grants nothing: second-years only supervise.

    The Task Supervisor is the exception to all of that — only a secretary or
    admin may change who holds it. Not the event coordinator whose work it
    oversees, and not a domain head either.
    """
    if task_name == TASK_SUPERVISOR:
        return roles.is_staff_side
    if roles.is_staff_side:
        return True
    if roles.is_domain_head and task_vertical and roles.domain_head_of == task_vertical:
        return True
    if coordinator_email and roles.email == str(coordinator_email).strip().lower():
        return True
    return False


def can_read_request(roles: PortalRoles, request_obj) -> bool:
    """Requester, coordinator, supervisor, secretary or admin."""
    if roles.is_staff_side:
        return True
    if not roles.email:
        # An anonymous caller has email "" — which would match any request whose
        # coordinator or supervisor field is blank (every Post request).
        return False
    return roles.email in {
        (request_obj.contact_email or "").lower(),
        (request_obj.coordinator_email or "").lower(),
        (request_obj.supervisor_email or "").lower(),
    }


def _more_than_cutoff_away(request_obj) -> bool:
    """True while the event is still more than EDIT_CUTOFF_HOURS from now."""
    if not request_obj.event_start:
        return False
    hours_until = (request_obj.event_start - timezone.now()).total_seconds() / HOUR
    return hours_until > EDIT_CUTOFF_HOURS


def _may_amend_schedule(roles: PortalRoles, request_obj) -> bool:
    if request_obj.type != RequestType.COVERAGE or request_obj.status in RequestStatus.TERMINAL:
        return False
    if not (roles.is_staff_side or roles.email == (request_obj.contact_email or "").lower()):
        return False
    return _more_than_cutoff_away(request_obj)


def can_change_event_time(roles: PortalRoles, request_obj) -> bool:
    """
    May this caller move a single-day Coverage request's start/end *times* (never
    its dates)? The requesting body, or the POC/Admin, and only while the event is
    still more than 24 hours away — the moment it is inside that window the
    team is effectively committed and only staff can sort things out by hand.

    A multi-day event has only dates, no times, so there is nothing to change.
    """
    if request_obj.is_multiday:
        return False
    return _may_amend_schedule(roles, request_obj)


def can_edit_subevents(roles: PortalRoles, request_obj) -> bool:
    """
    Who may add, edit or delete a Coverage request's sub-events: the requesting
    body or the POC/Admin. Whether a *particular* sub-event can still be touched
    is its own cutoff (`subevent_is_open`), not the main event's.
    """
    if request_obj.type != RequestType.COVERAGE or request_obj.status in RequestStatus.TERMINAL:
        return False
    return roles.is_staff_side or roles.email == (request_obj.contact_email or "").lower()


def subevent_is_open(start) -> bool:
    """True while a sub-event starting at `start` is still more than 48 hours away."""
    if not start:
        return False
    return (start - timezone.now()).total_seconds() / HOUR > SUBEVENT_CUTOFF_HOURS


def can_edit_venue(roles: PortalRoles, request_obj) -> bool:
    """
    Who may correct a Coverage request's venue after it's been submitted:
    the requesting committee itself (venues routinely move at the last
    minute and they're often the first to know), or whoever is already
    authorized to assign work on it (coordinator / domain head / secretary /
    admin — `can_assign`).
    """
    if roles.email == (request_obj.contact_email or "").lower():
        return True
    if can_assign(roles, "", request_obj.coordinator_email):
        return True
    # can_assign's domain-head branch needs one specific task's vertical —
    # a venue isn't tied to one, so a domain head qualifies here if they're
    # actually staffed on this request (mirrors assignment_list's own filter).
    return bool(roles.is_domain_head) and request_obj.tasks.filter(vertical=roles.domain_head_of).exists()


def can_view_meetings(roles: PortalRoles) -> bool:
    """
    May this account open the Meetings pages at all: team members (who see the
    meetings they're invited to) and the POC/Admin. One rule for both the menu
    link and the view, so hiding the link is never the only thing keeping a club
    or a role-less account out.
    """
    return bool(roles.is_authenticated and (roles.is_team or roles.is_staff_side))


def can_call_meeting(roles: PortalRoles) -> bool:
    """POC, Admin, or a vertical head may call a team meeting — of anyone on the team."""
    return bool(roles.is_authenticated and (roles.is_staff_side or roles.is_domain_head))


def can_manage_meeting(roles: PortalRoles, meeting) -> bool:
    """
    Who may edit, cancel or re-assign the MOM of a meeting, and mark its
    attendance: whoever called it, or the POC/Admin as a fallback.
    """
    if not roles.is_authenticated:
        return False
    return roles.is_staff_side or roles.email == (meeting.called_by or "").lower()


def can_take_attendance(roles: PortalRoles, meeting) -> bool:
    """Attendance is marked by whoever manages the meeting, once it has started and isn't cancelled."""
    return can_manage_meeting(roles, meeting) and meeting.has_started and not meeting.is_cancelled


def can_strike(roles: PortalRoles, member, color: str = STRIKE_YELLOW) -> bool:
    """
    May this caller manually give `member` a strike of this `color`?

    Secretary/admin may give either color to anyone. A domain head may give only
    a *yellow* strike, and only to a member of their own vertical — primary or
    secondary. Red is reserved to secretary/admin. Nobody else (including a plain
    event coordinator) may issue one; a strike is a reliability judgment about a
    person, not an action tied to one request.
    """
    if roles.is_staff_side:
        return True
    if color != STRIKE_YELLOW:
        return False
    return bool(roles.is_domain_head and roles.domain_head_of and member.in_vertical(roles.domain_head_of))
