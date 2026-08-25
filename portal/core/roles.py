"""
Server-side role resolution and assignment authorisation.

Ported from `server/src/engine/serverRoles.ts`. This is the authoritative role
source — the browser is never trusted for it. Roles are resolved from the
database on every request, so granting or revoking access takes effect
immediately and never requires a deploy (docs/PRD.md §4).
"""

from __future__ import annotations

from dataclasses import dataclass, field

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
    member: TeamMember | None = None
    committee: Committee | None = None
    names: list[str] = field(default_factory=list)

    @property
    def is_staff_side(self) -> bool:
        """Secretary or admin — the two roles that can act across the whole portal."""
        return self.is_secretary or self.is_admin

    @property
    def can_reach_assignments(self) -> bool:
        return (
            self.is_coordinator
            or self.is_domain_head
            or self.is_second_year
            or self.is_staff_side
        )


ANONYMOUS = PortalRoles()


def resolve_roles(email: str) -> PortalRoles:
    """Resolve the caller's roles from the database. `email` may be blank."""
    address = (email or "").strip().lower()
    if not address:
        return ANONYMOUS

    settings = PortalSettings.load()
    member = TeamMember.objects.filter(email=address).first()
    committee = Committee.objects.filter(email=address).first()

    # "Coordinator" isn't a stored flag — you are one if you currently coordinate
    # at least one request.
    from .models import Request  # local import: avoids a cycle at module load

    is_coordinator = Request.objects.filter(coordinator_email=address).exists()

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
        member=member,
        committee=committee,
        names=names,
    )


def can_assign(roles: PortalRoles, task_vertical: str, coordinator_email: str) -> bool:
    """
    May this caller assign or modify a task in `task_vertical` on a request
    coordinated by `coordinator_email`?

    Secretaries, admins and second-years may act anywhere. A domain head may act
    within their own vertical. An event coordinator may act on the events they
    coordinate. Ported verbatim from serverRoles.ts's `canAssign`.
    """
    if roles.is_staff_side:
        return True
    if roles.is_second_year:
        return True
    if roles.is_domain_head and task_vertical and roles.domain_head_of == task_vertical:
        return True
    if coordinator_email and roles.email == str(coordinator_email).strip().lower():
        return True
    return False


def can_read_request(roles: PortalRoles, request_obj) -> bool:
    """Requester, coordinator, secretary or admin. Mirrors routes/requests.ts."""
    if roles.is_staff_side:
        return True
    return roles.email in {
        (request_obj.contact_email or "").lower(),
        (request_obj.coordinator_email or "").lower(),
    }


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
    return can_assign(roles, "", request_obj.coordinator_email)


def can_strike(roles: PortalRoles, member) -> bool:
    """
    May this caller manually issue a strike to `member`?

    Secretary/admin may strike anyone. A domain head may only strike members
    of their own vertical — the same scoping `can_assign` uses for task
    authorization, kept consistent here rather than inventing a separate rule.
    Nobody else (including a plain event coordinator) may issue one; unlike
    task assignment, a strike is a reliability judgment about a person, not an
    action tied to one request.
    """
    if roles.is_staff_side:
        return True
    if roles.is_domain_head and roles.domain_head_of and member.vertical == roles.domain_head_of:
        return True
    return False
