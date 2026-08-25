"""
Google Calendar integration (docs/PRD.md §5.12).

Ported from `server/src/services/calendar.ts`, keeping both of its important
behaviours:

* **Auto-fallback.** With no service-account credentials configured, every call
  becomes a logged no-op. Development and production therefore run identical
  code paths, and the portal works fine before the Workspace delegation this
  needs is provisioned (docs/PIC.md §3 records it as outstanding).

* **Fail open.** If a calendar lookup errors, `is_free` returns True. A flaky
  Google API must never be the reason an event goes unstaffed — a double-booked
  photographer is a smaller problem than no photographer.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path

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
    """Used whenever credentials aren't configured. Everything is free."""

    configured = False

    def is_free(self, email: str, start: datetime, end: datetime) -> bool:
        return True

    def create_hold(self, email: str, title: str, start: datetime, end: datetime, description: str = "") -> None:
        logger.info("[calendar:stub] hold -> %s | %s", email, title)

    def create_reminder(self, email: str, title: str, due: datetime, description: str = "") -> None:
        logger.info("[calendar:stub] reminder -> %s | %s", email, title)


class GoogleCalendar:
    """
    Real implementation: a service account with domain-wide delegation,
    impersonating each member so events land on their own primary calendar.
    """

    configured = True

    def __init__(self, credentials: dict):
        self._credentials = credentials

    def _service(self, user_email: str):
        from google.oauth2 import service_account
        from googleapiclient.discovery import build

        creds = service_account.Credentials.from_service_account_info(
            self._credentials, scopes=SCOPES
        ).with_subject(user_email)
        return build("calendar", "v3", credentials=creds, cache_discovery=False)

    def is_free(self, email: str, start: datetime, end: datetime) -> bool:
        try:
            response = (
                self._service(email)
                .freebusy()
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
            self._service(email).events().insert(
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


def _load_credentials(raw: str) -> dict:
    """`raw` is either inline JSON or a path to a service-account key file."""
    value = raw.strip()
    if value.startswith("{"):
        return json.loads(value)
    return json.loads(Path(value).read_text(encoding="utf-8"))


_cached = None


def calendar_service():
    """The configured calendar backend. Chosen once, then reused."""
    global _cached
    if _cached is not None:
        return _cached

    raw = getattr(settings, "CALENDAR_SERVICE_ACCOUNT_JSON", "")
    if not raw:
        _cached = StubCalendar()
        return _cached

    try:
        _cached = GoogleCalendar(_load_credentials(raw))
        logger.info("calendar: using Google Calendar")
    except Exception as exc:
        logger.error("calendar: bad configuration, falling back to stub: %s", exc)
        _cached = StubCalendar()
    return _cached


def reset_cache() -> None:
    """Test hook — forces the next call to re-read configuration."""
    global _cached
    _cached = None
