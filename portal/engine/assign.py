"""
Assignment eligibility and selection (docs/PRD.md §5.2).

Ported from `server/src/engine/assign.ts`. Pure logic over an in-memory list of
members — the caller loads the roster and passes it in — so the selection rules
can be tested exhaustively without database setup.

A member is eligible when they are active, not marked out of work, below the
strike limit, hold the required skill, and (when campus_strict is on and the
request has a campus) are on the same campus. For at-event tasks, anyone whose
calendar shows them busy during the event window is then excluded.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.constants import Availability


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
    member, required_skill: str, request_obj, settings, *, require_skill: bool = True
) -> bool:
    """
    Everything except the calendar check, which costs a network call.

    `require_skill=False` drops the skill/vertical match — used only for a
    deliberate manual reassignment, where a coordinator may need to pull in
    someone from another vertical as a stopgap. Active/availability/strikes/
    campus still apply either way; those aren't a "vertical" restriction.
    """
    return (
        member.active
        and member.availability != Availability.OUT
        and (member.strikes or 0) < settings.strike_limit
        and (not require_skill or required_skill in (member.skills or []))
        and campus_ok(member, request_obj, settings)
    )


def _fairness_key(member):
    """
    Order the pool so the least-loaded person comes first: fewest points, then
    fewest strikes, then name for a stable tie-break. This is what keeps work
    spread across the team instead of landing on whoever matches first
    (docs/PRD.md §5.2).
    """
    return (member.points or 0, member.strikes or 0, str(member.name))


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
) -> list:
    """The full sorted pool for a required skill, best candidate first."""
    excluded = {e.lower() for e in (exclude or set())}
    pool = [
        m
        for m in team
        if is_base_eligible(m, required_skill, request_obj, settings, require_skill=require_skill)
        and m.email.lower() not in excluded
    ]

    if at_event and request_obj.event_start and request_obj.event_end:
        pool = [
            m
            for m in pool
            if calendar.is_free(m.email, request_obj.event_start, request_obj.event_end)
        ]

    return sorted(pool, key=_fairness_key)


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
    )

    if not pool:
        where = ""
        if settings.campus_strict and request_obj.campus:
            where = f" on {request_obj.campus}"
        return Choice(
            member=None,
            reason=f'no active member with skill "{pipeline_task.required_skill}"{where}',
        )

    # Role exclusivity: prefer someone not already on this request, so one person
    # doesn't end up shooting, editing and vetting the same event. If everyone
    # eligible is already on it, doubling up beats leaving the role unfilled.
    fresh = [m for m in pool if m.email not in already_assigned]
    return Choice(member=(fresh or pool)[0], reason="")
