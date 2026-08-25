"""
Sign-in policy.

Replaces the verify callback in `server/src/auth/passport.ts`. Two jobs:

1. **Enforce the institute-domain gate, authoritatively.** The `hd` parameter we
   send to Google only pre-filters its account chooser; Google documents it as a
   hint and it can be worked around. So the real check happens here, on our
   side, after the handshake and before any session exists.

2. **Mirror admin/secretary rights onto the Django user** so that whoever holds
   those roles in PortalSettings can also reach /admin/, without maintaining a
   second list of people.
"""

from __future__ import annotations

import logging

from allauth.account.adapter import DefaultAccountAdapter
from allauth.core.exceptions import ImmediateHttpResponse
from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.conf import settings as django_settings
from django.contrib import messages
from django.shortcuts import redirect

logger = logging.getLogger(__name__)


def allowed_domains() -> list[str]:
    """
    Domains permitted to sign in.

    Read from PortalSettings so it's editable without a deploy, falling back to
    the settings value before the first seed has run (or if the table is
    somehow unreadable — better to fail closed onto a sane default than to open
    the portal up).
    """
    try:
        from core.models import PortalSettings

        configured = [
            str(d).strip().lower()
            for d in (PortalSettings.load().allowed_domains or [])
            if str(d).strip()
        ]
        if configured:
            return configured
    except Exception as exc:  # pragma: no cover - only before migrations exist
        logger.warning("Falling back to settings.ALLOWED_EMAIL_DOMAINS: %s", exc)
    return [d.lower() for d in django_settings.ALLOWED_EMAIL_DOMAINS]


def is_allowed_email(email: str) -> bool:
    address = (email or "").strip().lower()
    if "@" not in address:
        return False
    return address.rsplit("@", 1)[1] in allowed_domains()


class PortalAccountAdapter(DefaultAccountAdapter):
    """The portal has no passwords — Google is the only way in."""

    def is_open_for_signup(self, request) -> bool:
        return False  # signup only ever happens via the social adapter


class PortalSocialAccountAdapter(DefaultSocialAccountAdapter):
    def is_open_for_signup(self, request, sociallogin) -> bool:
        return is_allowed_email(_email_of(sociallogin))

    def pre_social_login(self, request, sociallogin):
        """
        Runs after Google authenticates but before a session is created — the
        right place to refuse someone, since nothing has been committed yet.
        """
        email = _email_of(sociallogin)
        if not is_allowed_email(email):
            domains = ", ".join(allowed_domains())
            logger.warning("Refused sign-in for %r (allowed: %s)", email, domains)
            messages.error(
                request,
                f"Only {domains} accounts can use this portal. "
                f"You signed in as {email or 'an unknown account'}.",
            )
            raise ImmediateHttpResponse(redirect("signed-out"))

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        _sync_staff_flags(user)
        return user

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        # Django's username field is 150 chars and unique; the email is the real
        # identity here, so use it verbatim and keep the two in step.
        email = (data.get("email") or "").strip().lower()
        if email:
            user.email = email
            user.username = email[:150]
        return user


def _email_of(sociallogin) -> str:
    email = (sociallogin.user.email or "").strip().lower()
    if email:
        return email
    extra = sociallogin.account.extra_data or {}
    return str(extra.get("email", "")).strip().lower()


def _sync_staff_flags(user) -> None:
    """
    Keep `is_staff` / `is_superuser` in step with the admin and secretary lists.

    Called on every login (see the signal in accounts/signals.py) so that adding
    someone to PortalSettings.admin_emails grants Django admin access the next
    time they sign in — no second list to maintain, no deploy.
    """
    from core.roles import resolve_roles

    roles = resolve_roles(user.email or user.get_username())
    desired_staff = roles.is_staff_side
    desired_super = roles.is_admin

    # Never demote a manually-created break-glass superuser.
    if user.is_superuser and not desired_super:
        return

    if user.is_staff != desired_staff or user.is_superuser != desired_super:
        user.is_staff = desired_staff
        user.is_superuser = desired_super
        user.save(update_fields=["is_staff", "is_superuser"])
