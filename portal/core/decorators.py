"""
View-access decorators.

The direct equivalents of the Express middleware chain
`requireAuth, attachRoles, requireSecretaryOrAdmin` — but here `request.roles`
is already attached to every request by PortalRolesMiddleware, so these just
gate on it.
"""

from __future__ import annotations

from functools import wraps

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import redirect


def portal_login_required(view):
    """Alias kept separate from Django's so call sites read as portal-specific."""
    return login_required(view)


def secretary_or_admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.roles.is_staff_side:
            raise PermissionDenied("Secretaries and admins only.")
        return view(request, *args, **kwargs)

    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.roles.is_admin:
            raise PermissionDenied("Admins only.")
        return view(request, *args, **kwargs)

    return wrapped


def team_required(view):
    """Media-team members only (My Tasks)."""

    @wraps(view)
    @login_required
    def wrapped(request, *args, **kwargs):
        if not request.roles.is_team:
            messages.error(request, "Your account isn't registered to the media team.")
            return redirect("home")
        return view(request, *args, **kwargs)

    return wrapped
