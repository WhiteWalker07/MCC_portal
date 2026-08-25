"""
Role-gated navigation, available to every template.

Ported from the `NAV` array in `web/js/shell.js`. A user sees the union of the
items their roles allow. Keeping this in one list means the nav bar and the
access checks on the views can't drift apart — each entry names the predicate
that also guards its view.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from core.roles import PortalRoles


@dataclass(frozen=True)
class NavItem:
    id: str
    label: str
    short: str
    url_name: str
    visible: Callable[[PortalRoles], bool]


NAV_ITEMS: list[NavItem] = [
    # Any signed-in institute account can raise a Post request; Coverage is
    # restricted to committees, enforced in the form and the view.
    NavItem("new", "New Request", "New", "request-new", lambda r: True),
    NavItem("requests", "My Requests", "Requests", "request-list", lambda r: True),
    NavItem("tasks", "My Tasks", "Tasks", "task-list", lambda r: r.is_team),
    NavItem("assignments", "Assignments", "Assign", "assignment-list", lambda r: r.can_reach_assignments),
    NavItem("approvals", "Approvals", "Approve", "approval-list", lambda r: r.is_secretary),
    NavItem("dashboard", "Dashboard", "Stats", "dashboard", lambda r: r.is_staff_side),
    NavItem("admin", "Admin", "Admin", "portal-admin", lambda r: r.is_staff_side),
]

ROLE_LABELS = {
    "committee": "Committee",
    "team": "Team",
    "coordinator": "Coordinator",
    "domainHead": "Domain Head",
    "secretary": "Secretary",
    "admin": "Admin",
}


def visible_nav(roles: PortalRoles) -> list[NavItem]:
    if not roles.is_authenticated:
        return []
    return [item for item in NAV_ITEMS if item.visible(roles)]


def identity_tag(roles: PortalRoles) -> str:
    """The subtitle beside the brand: which committee or team you're acting as."""
    if roles.committee:
        parts = [roles.committee.name, roles.committee.acronym, roles.committee.campus]
        return " · ".join(p for p in parts if p)

    parts: list[str] = []
    if roles.member:
        bits = ["Media Team"]
        if roles.member.campus:
            bits.append(roles.member.campus)
        if roles.domain_head_of:
            bits.append(f"Head of {roles.domain_head_of}")
        parts.append(" · ".join(bits))
    if roles.is_secretary:
        parts.append("Secretary (POC)")
    if roles.is_admin:
        parts.append("Admin")
    return " · ".join(parts) if parts else "Account not registered"


def portal_nav(request):
    roles = getattr(request, "roles", None)
    if roles is None or not roles.is_authenticated:
        return {"nav_items": [], "portal_roles": roles, "role_badges": [], "identity_tag": ""}
    return {
        "nav_items": visible_nav(roles),
        "portal_roles": roles,
        "role_badges": [ROLE_LABELS.get(name, name) for name in roles.names],
        "identity_tag": identity_tag(roles),
    }
