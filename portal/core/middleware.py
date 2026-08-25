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
        return self.get_response(request)

    @staticmethod
    def _resolve(request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            return ANONYMOUS
        return resolve_roles(user.email or user.get_username())
