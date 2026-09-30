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
"""

from __future__ import annotations

from dataclasses import dataclass

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
    name for a stable tie-break. This is what keeps work spread across the team
    instead of landing on whoever matches first (docs/PRD.md §5.2).
    """
    return (member.points or 0, str(member.name))


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
) -> list:
    """
    The full sorted pool for a role, best candidate first.

    When the task belongs to a `vertical`, members are ordered primary-vertical
    first, then secondary-vertical, then everyone else who can do it; the
    fairness order applies within each of those groups.
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
    # free for it" isn't a meaningful question: nobody's calendar is clear of
    # everything across several days.
    if at_event and request_obj.event_start and request_obj.event_end and not request_obj.is_multiday:
        pool = [
            m
            for m in pool
            if calendar.is_free(m.email, request_obj.event_start, request_obj.event_end)
        ]

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
    return Choice(member=(fresh or pool)[0], reason="")


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
    return Choice(member=pool[0], reason="")
