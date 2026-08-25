"""
Append-only activity log.

Ported from `server/src/lib/log.ts`. Best-effort by design: an audit write must
never be the thing that fails a request the user actually cared about, so
failures are logged and swallowed.
"""

from __future__ import annotations

import logging

from .models import ActivityLog

logger = logging.getLogger(__name__)


def log_activity(
    event: str,
    *,
    request_obj=None,
    ref_code: str = "",
    actor: str = "",
    member: str = "",
    detail: str = "",
) -> None:
    try:
        ActivityLog.objects.create(
            event=event,
            request=request_obj,
            ref_code=ref_code or (getattr(request_obj, "ref_code", "") or ""),
            actor=actor,
            member=member,
            detail=detail[:500],
        )
    except Exception as exc:  # pragma: no cover - defensive, mirrors the original
        logger.error("activity log append failed (%s): %s", event, exc)
