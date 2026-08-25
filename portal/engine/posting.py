"""
Post slot scheduling (docs/PRD.md §5.6).

Finds the next free publishing slot (11:00 / 14:00 / 17:00 by default) for a
platform that isn't already taken by another scheduled post on that platform.

Ported from `server/src/engine/posting.ts`, with one deliberate change: the
original hard-coded `IST_OFFSET_MIN = 330` and did the UTC arithmetic by hand.
That is correct today but silently wrong if India ever adopts DST or the slot
list moves. This version uses `zoneinfo("Asia/Kolkata")`, so slots are true IST
wall-clock times and the conversion is the standard library's problem.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")

DEFAULT_SLOTS = [time(11, 0), time(14, 0), time(17, 0)]

#: How far ahead to look before giving up. Three months of slots is far beyond
#: any real backlog; the bound just stops a pathological config from looping.
SEARCH_DAYS = 90


def find_next_slot(slots, taken: set[datetime], now: datetime) -> datetime:
    """
    First slot strictly after `now` that isn't in `taken`.

    `slots` are IST wall-clock times; `taken` holds already-booked, timezone-aware
    datetimes for the platform. The return value is timezone-aware.
    """
    ordered = sorted(slots) if slots else list(DEFAULT_SLOTS)
    if not ordered:
        ordered = list(DEFAULT_SLOTS)

    taken_instants = {dt.astimezone(IST) for dt in taken if dt is not None}
    today_ist: date = now.astimezone(IST).date()

    for day_offset in range(SEARCH_DAYS):
        day = today_ist + timedelta(days=day_offset)
        for slot in ordered:
            candidate = datetime.combine(day, slot, tzinfo=IST)
            if candidate > now.astimezone(IST) and candidate not in taken_instants:
                return candidate

    # Unreachable with any sane config; fall back to this time tomorrow rather
    # than returning None and pushing the failure into the caller.
    return (now + timedelta(days=1)).astimezone(IST)
