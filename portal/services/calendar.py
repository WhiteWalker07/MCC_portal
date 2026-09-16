"""
Google Calendar integration (docs/PRD.md §5.12).

Per-member OAuth consent, not a service account: IT declined the Workspace
domain-wide delegation a service account would need, so instead each team
member grants Calendar access themselves through the same Google sign-in the
portal already uses (see `SOCIALACCOUNT_PROVIDERS` in settings.py). Their
token is persisted by django-allauth (`SOCIALACCOUNT_STORE_TOKENS`) and looked
up here by email — every call site in this codebase checks or writes a
*different* member's calendar than whoever is logged in (auto-assignment,
coordinator reassignment, the deadline cron), so there is no "current user"
token to reuse.

Two fallback layers, both intentional, both ported from the old design:

* **Auto-fallback.** With `CALENDAR_ENABLED` off, every call becomes a logged
  no-op — dev/test runs the same code paths as production without hitting
  Google.
* **Fail open, per member.** A member who hasn't (re)consented yet, or whose
  token fails to refresh (e.g. revoked from
  myaccount.google.com/permissions), is treated as free/no-op for *them
  only* — one person's missing consent must never block the whole assignment
  pipeline. A flaky Google API must never be the reason an event goes
  unstaffed — a double-booked photographer is a smaller problem than no
  photographer.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.conf import settings

logger = logging.getLogger(__name__)

TIMEZONE = "Asia/Kolkata"
SCOPES = [
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/calendar.events",
]

#: How long a deadline reminder occupies the assignee's calendar.
REMINDER_MINUTES = 30


class StubCalendar:
    """Used whenever CALENDAR_ENABLED is off. Everything is free."""

    configured = False

    def is_free(self, email: str, start: datetime, end: datetime) -> bool:
        return True

    def create_hold(self, email: str, title: str, start: datetime, end: datetime, description: str = "") -> None:
        logger.info("[calendar:stub] hold -> %s | %s", email, title)

    def create_reminder(self, email: str, title: str, due: datetime, description: str = "") -> None:
        logger.info("[calendar:stub] reminder -> %s | %s", email, title)


class GoogleCalendar:
    """
    Real implementation: looks up each member's own stored Google OAuth token
    (granted at their login) and acts on their calendar with it — no service
    account, no impersonation.
    """

    configured = True

    def _credentials_for(self, email: str):
        """
        The member's live `Credentials`, or `None` if they haven't granted
        Calendar access yet (no stored refresh token) — callers treat that as
        the fail-open case, not an error.
        """
        from allauth.socialaccount.models import SocialAccount, SocialToken
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials

        try:
            account = SocialAccount.objects.get(user__email__iexact=email, provider="google")
            token = SocialToken.objects.get(account=account)
        except (SocialAccount.DoesNotExist, SocialToken.DoesNotExist):
            return None
        if not token.token_secret:
            # No refresh token stored yet -- they signed in before the
            # calendar scope existed, or haven't logged in since
            # CALENDAR_ENABLED was turned on. They'll get one next time they
            # sign in (access_type=offline + prompt=consent, see settings.py).
            return None

        app = settings.SOCIALACCOUNT_PROVIDERS["google"]["APP"]
        creds = Credentials(
            token=token.token,
            refresh_token=token.token_secret,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=app["client_id"],
            client_secret=app["secret"],
            scopes=SCOPES,
        )
        # Always refresh rather than trusting locally-cached expiry: it's one
        # cheap round trip, and it self-heals a stale/incorrect `expires_at`
        # instead of surfacing a 401 to the caller. A RefreshError (token
        # revoked) propagates up to is_free/_insert's own try/except, which
        # already fail open.
        creds.refresh(Request())
        token.token = creds.token
        token.expires_at = creds.expiry
        token.save(update_fields=["token", "expires_at"])
        return creds

    def _service(self, user_email: str):
        from googleapiclient.discovery import build

        creds = self._credentials_for(user_email)
        if creds is None:
            return None
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def is_free(self, email: str, start: datetime, end: datetime) -> bool:
        try:
            service = self._service(email)
            if service is None:
                logger.info("calendar: %s hasn't granted Calendar access yet, treating as free", email)
                return True
            response = (
                service.freebusy()
                .query(
                    body={
                        "timeMin": start.isoformat(),
                        "timeMax": end.isoformat(),
                        "items": [{"id": "primary"}],
                    }
                )
                .execute()
            )
            busy = response.get("calendars", {}).get("primary", {}).get("busy", [])
            return not busy
        except Exception as exc:
            logger.error("calendar is_free(%s) failed, treating as free: %s", email, exc)
            return True

    def create_hold(self, email: str, title: str, start: datetime, end: datetime, description: str = "") -> None:
        self._insert(email, title, start, end, description)

    def create_reminder(self, email: str, title: str, due: datetime, description: str = "") -> None:
        self._insert(email, title, due, due + timedelta(minutes=REMINDER_MINUTES), description)

    def _insert(self, email: str, title: str, start: datetime, end: datetime, description: str) -> None:
        try:
            service = self._service(email)
            if service is None:
                logger.info("[calendar:no-consent] hold/reminder skipped -> %s | %s", email, title)
                return
            service.events().insert(
                calendarId="primary",
                body={
                    "summary": title,
                    "description": description or "",
                    "start": {"dateTime": start.isoformat(), "timeZone": TIMEZONE},
                    "end": {"dateTime": end.isoformat(), "timeZone": TIMEZONE},
                },
            ).execute()
        except Exception as exc:
            logger.error("calendar insert(%s, %r) failed: %s", email, title, exc)


_cached = None


def calendar_service():
    """The configured calendar backend. Chosen once, then reused."""
    global _cached
    if _cached is not None:
        return _cached

    if not getattr(settings, "CALENDAR_ENABLED", False):
        _cached = StubCalendar()
        return _cached

    _cached = GoogleCalendar()
    logger.info("calendar: using per-member Google Calendar (OAuth consent)")
    return _cached


def reset_cache() -> None:
    """Test hook — forces the next call to re-read configuration."""
    global _cached
    _cached = None
