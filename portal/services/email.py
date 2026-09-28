"""
Outbound email.

Replaces `server/src/services/email.ts`. That module hand-rolled provider
selection (Resend when an API key was present, a logging stub otherwise);
Django's EMAIL_BACKEND setting already does exactly that, so this is only a thin
wrapper over `EmailMessage` that preserves the one behaviour that mattered:

    **a failed notification must never break the workflow that triggered it.**

A request is accepted, points are awarded and a task is assigned whether or not
the mail relay is reachable. Failures are logged and swallowed.

Every message also gets an explicit system-generated disclaimer appended --
nothing sent from here should read as if a person typed it.
"""

from __future__ import annotations

import logging
from email.utils import parseaddr

from django.conf import settings
from django.core.mail import EmailMessage

logger = logging.getLogger(__name__)

DISCLAIMER = (
    "\n\n---\n"
    "This is a system-generated email from the MCC Portal. Please do not reply to it directly."
)


def _clean(value) -> list[str]:
    """
    `value` may be a string, an iterable of strings, or None/blank.
    Deduplicates case-insensitively, first occurrence wins -- one person
    holding two roster roles (e.g. Event Coordinator and Photographer on a
    small team) must not get CC'd twice.
    """
    if not value:
        return []
    items = [value] if isinstance(value, str) else list(value)
    seen: set[str] = set()
    cleaned: list[str] = []
    for address in items:
        address = (address or "").strip()
        if not address or address.lower() in seen:
            continue
        seen.add(address.lower())
        cleaned.append(address)
    return cleaned


def thread_id_for(ref_code: str) -> str:
    """
    A stable Message-ID for a request's first club-facing email ([Accepted]),
    derived purely from its ref_code. Later emails about the same request
    (a reassignment, coverage being shared) pass this back as `in_reply_to`
    so mail clients (Gmail included) group them into one thread -- no new
    field on Request needed, since ref_code is already unique and permanent.
    """
    _, address = parseaddr(settings.DEFAULT_FROM_EMAIL)
    domain = address.split("@", 1)[-1] if "@" in address else "mcc-portal.local"
    return f"<{ref_code}@{domain}>"


def send(to, subject: str, body: str, *, cc=None, message_id=None, in_reply_to=None) -> None:
    """
    Send one plain-text message. `to`/`cc` may each be a string or an
    iterable.

    `message_id` gives this message an explicit, caller-chosen Message-ID
    (used to start a thread -- see `thread_id_for`). `in_reply_to` threads
    this message under an earlier one's Message-ID (sets both In-Reply-To
    and References, which is what mail clients actually key threading off).
    """
    recipients = _clean(to)
    if not recipients:
        return
    already_to = {address.lower() for address in recipients}
    cc_list = [address for address in _clean(cc) if address.lower() not in already_to]

    headers = {}
    if message_id:
        headers["Message-ID"] = message_id
    if in_reply_to:
        headers["In-Reply-To"] = in_reply_to
        headers["References"] = in_reply_to

    try:
        EmailMessage(
            subject=subject,
            body=body.rstrip() + DISCLAIMER,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=recipients,
            cc=cc_list or None,
            headers=headers or None,
        ).send(fail_silently=False)
    except Exception as exc:
        logger.error(
            "email failed (%s -> %s, cc %s): %s",
            subject,
            ", ".join(recipients),
            ", ".join(cc_list) or "-",
            exc,
        )
