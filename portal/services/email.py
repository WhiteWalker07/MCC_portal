"""
Outbound email.

Replaces `server/src/services/email.ts`. That module hand-rolled provider
selection (Resend when an API key was present, a logging stub otherwise);
Django's EMAIL_BACKEND setting already does exactly that, so this is only a thin
wrapper over `send_mail` that preserves the one behaviour that mattered:

    **a failed notification must never break the workflow that triggered it.**

A request is accepted, points are awarded and a task is assigned whether or not
the mail relay is reachable. Failures are logged and swallowed.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import send_mail

logger = logging.getLogger(__name__)


def send(to, subject: str, body: str) -> None:
    """Send one plain-text message. `to` may be a string or an iterable."""
    recipients = [to] if isinstance(to, str) else list(to or [])
    recipients = [address.strip() for address in recipients if address and address.strip()]
    if not recipients:
        return

    try:
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
    except Exception as exc:
        logger.error("email failed (%s -> %s): %s", subject, ", ".join(recipients), exc)
