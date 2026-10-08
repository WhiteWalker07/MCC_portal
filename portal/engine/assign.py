"""
Assignment eligibility and selection (docs/PRD.md §5.2).

Ported from `server/src/engine/assign.ts`. Pure logic over an in-memory list of
members — the caller loads the roster and passes it in — so the selection rules
can be tested exhaustively without database setup.

A member is eligible when they are active, not marked out of work, hold the
required skill (a task type with no required skill accepts anyone) and (when
campus_strict is on and the request has a campus) are on the same campus. For
at-event tasks, anyone whose calendar shows them busy during the event window
is then excluded.

**Second-years only supervise.** A second-year is eligible for the Task
Supervisor role and for nothing else; every other role goes to first-years.
That rule lives here, in `is_base_eligible`, so auto-assignment, manual
reassignment and the eligibility lists on every form all obey it identically.

Strikes deliberately play no part in eligibility or ordering — they are a
record kept by heads and the POC, not a lever on the assignment engine.

**Choosing among equals.** Candidates are ranked by vertical tier, then by points
(fewest first). If several are *still* tied after that, the pick is made at random:
a last resort, so that a tie is never settled by who happens to come first in the
alphabet. The lists shown to people (the manual-assign dropdowns) stay sorted by
name, because that is only for reading; only the automatic pick is randomised
(`pick_best`; switched by `settings.PORTAL_RANDOM_TIE_BREAK`).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from django.conf import settings as django_settings

from core.constants import TASK_SUPERVISOR, Availability


@dataclass(frozen=True)
class Choice:
    member: object | None
    reason: str


def campus_ok(member, request_obj, settings) -> bool:
    if not settings.campus_strict:
        return True
    if not request_obj.campus:
        return True
    if not member.campus:
        return True  # a blank campus stays eligible during roster rollout
    return member.campus == request_obj.campus


def is_base_eligible(
    member,
    required_skill: str,
    request_obj,
    settings,
    *,
    require_skill: bool = True,
    task_name: str = "",
) -> bool:
    """
    Everything except the calendar check, which costs a network call.

    `require_skill=False` drops the skill match — used only for a deliberate
    manual reassignment, where a coordinator may need to pull in someone with a
    different skill as a stopgap. Active/availability/campus and the
    year rule still apply either way; those aren't a skill restriction.

    `task_name` decides which side of the year rule applies: second-years for
    the Task Supervisor, first-years for everything else. Leaving it blank
    means "an ordinary task", which is the safe failure mode — it can never
    accidentally hand a second-year regular work.
    """
    wants_second_year = task_name == TASK_SUPERVISOR
    return (
        member.active
        and member.availability != Availability.OUT
        and (member.year == 2) == wants_second_year
        and (not require_skill or not required_skill or required_skill in (member.skills or []))
        and campus_ok(member, request_obj, settings)
    )


def _fairness_key(member):
    """
    Order the pool so the least-loaded person comes first: fewest points, then
    name. The name only makes the *displayed* order stable; it never decides an
    automatic pick (see `pick_best`). This is what keeps work spread across the
    team instead of landing on whoever matches first (docs/PRD.md §5.2).
    """
    return (member.points or 0, str(member.name))


def _choose_among_tied(tied: list):
    """One of several equally good candidates: random, unless switched off (tests)."""
    if len(tied) == 1 or not getattr(django_settings, "PORTAL_RANDOM_TIE_BREAK", True):
        return tied[0]
    return random.choice(tied)


def pick_best(pool: list, rank=None, *, vertical: str = ""):
    """
    The automatic pick from `pool`, which is sorted best-first (as `eligible_members`
    returns it). Everyone tied with the first on `rank` is equally good, and one of
    them is chosen at random; `None` for an empty pool.

    `rank` defaults to (vertical tier, points): the real criteria, without the name.
    """
    if not pool:
        return None
    rank = rank or (lambda m: (_vertical_tier(m, vertical), m.points or 0))
    best = rank(pool[0])
    return _choose_among_tied([m for m in pool if rank(m) == best])


def _vertical_tier(member, vertical: str) -> int:
    """0 = the task's own vertical is their primary, 1 = their secondary, 2 = neither."""
    if not vertical:
        return 0
    if member.vertical == vertical:
        return 0
    if member.secondary_vertical == vertical:
        return 1
    return 2


def eligible_members(
    required_skill: str,
    at_event: bool,
    request_obj,
    settings,
    team,
    calendar,
    exclude: set[str] | None = None,
    *,
    require_skill: bool = True,
    task_name: str = "",
    vertical: str = "",
    window: tuple | None = None,
) -> list:
    """
    The full sorted pool for a role, best candidate first.

    When the task belongs to a `vertical`, members are ordered primary-vertical
    first, then secondary-vertical, then everyone else who can do it; the
    fairness order applies within each of those groups.

    `window` is the (start, end) a person must be free for. It is the sub-event's
    own time for a task that covers one sub-event; otherwise it is the whole
    event's, except for a multi-day event, which has no single window to check.
    """
    excluded = {e.lower() for e in (exclude or set())}
    pool = [
        m
        for m in team
        if is_base_eligible(
            m, required_skill, request_obj, settings, require_skill=require_skill, task_name=task_name
        )
        and m.email.lower() not in excluded
    ]

    # A multi-day event is whole days with no single time window, so "is this person
    # free for it" isn't a meaningful question for its whole-event tasks: nobody's
    # calendar is clear of everything across several days. A task that covers one
    # sub-event does have a real window, and is checked against it.
    if window is None and request_obj.event_start and request_obj.event_end and not request_obj.is_multiday:
        window = (request_obj.event_start, request_obj.event_end)
    if at_event and window and window[0] and window[1]:
        start, end = window
        pool = [m for m in pool if calendar.is_free(m.email, start, end)]

    return sorted(pool, key=lambda m: (_vertical_tier(m, vertical), *_fairness_key(m)))


def choose_member(
    pipeline_task,
    request_obj,
    settings,
    team,
    already_assigned: set[str],
    calendar,
) -> Choice:
    """Pick the best available member for one pipeline task."""
    pool = eligible_members(
        pipeline_task.required_skill,
        pipeline_task.at_event,
        request_obj,
        settings,
        team,
        calendar,
        task_name=pipeline_task.task,
        vertical=pipeline_task.vertical,
        window=pipeline_task.window,
    )

    if not pool:
        where = ""
        if settings.campus_strict and request_obj.campus:
            where = f" on {request_obj.campus}"
        what = (
            "no active second-year"
            if pipeline_task.task == TASK_SUPERVISOR
            else f'no active first-year with skill "{pipeline_task.required_skill}"'
            if pipeline_task.required_skill
            else "no active first-year"
        )
        return Choice(member=None, reason=f"{what}{where}")

    # Role exclusivity: prefer someone not already on this request, so one person
    # doesn't end up shooting and editing the same event. If everyone eligible is
    # already on it, doubling up beats leaving the role unfilled.
    fresh = [m for m in pool if m.email not in already_assigned]
    return Choice(member=pick_best(fresh or pool, vertical=pipeline_task.vertical), reason="")


def choose_supervisor(request_obj, settings, team, open_counts: dict[str, int]) -> Choice:
    """
    Pick the Task Supervisor: the second-year currently overseeing the fewest
    open requests. No calendar check — a supervisor isn't at the event — and no
    skill match; being a second-year is the whole qualification.

    `open_counts` maps a member's email to how many unfinished supervisions they
    hold (see engine.workflow.open_supervision_counts).
    """
    pool = [
        m
        for m in team
        if is_base_eligible(m, "", request_obj, settings, require_skill=False, task_name=TASK_SUPERVISOR)
    ]
    if not pool:
        where = ""
        if settings.campus_strict and request_obj.campus:
            where = f" on {request_obj.campus}"
        return Choice(member=None, reason=f"no active second-year{where}")

    pool.sort(key=lambda m: (open_counts.get(m.email.lower(), 0), *_fairness_key(m)))
    # Fewest open supervisions, then fewest points; still tied -> at random.
    return Choice(
        member=pick_best(pool, lambda m: (open_counts.get(m.email.lower(), 0), m.points or 0)), reason=""
    )
