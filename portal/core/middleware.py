"""
Attaches the caller's resolved portal roles to every request.

The Express equivalent was the `attachRoles` middleware applied per-route. Here
it runs once for every request and caches the result on the request object, so
views, templates and the nav context processor all read the same snapshot
without re-querying.
"""

from __future__ import annotations

from django.utils.functional import SimpleLazyObject

from .roles import ANONYMOUS, resolve_roles


class PortalRolesMiddleware:
    """
    Sets `request.roles` to a `PortalRoles`.

    Lazily evaluated: anonymous pages (the sign-in screen, static files) never
    pay for the queries. Must come after AuthenticationMiddleware, which is what
    puts `request.user` in place.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.roles = SimpleLazyObject(lambda: self._resolve(request))
        if request.path.startswith("/admin/"):
            self._refresh_staff_flags(request)
        return self.get_response(request)

    @staticmethod
    def _refresh_staff_flags(request):
        """
        Django admin trusts the stored is_staff/is_superuser flags, which are
        otherwise only synced at sign-in — and the rolling session keeps an active
        user signed in for weeks. Re-sync them on every admin request so a revoked
        role stops working immediately, as the rest of the portal already does.
        """
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            from accounts.adapters import _sync_staff_flags

            _sync_staff_flags(user)

    @staticmethod
    def _resolve(request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return ANONYMOUS
        return resolve_roles(user.email or user.get_username())
