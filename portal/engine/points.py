"""
Scoring scheme (docs/PRD.md §5.7).

Ported verbatim from `server/src/engine/points.ts`. Pure arithmetic — no
database access — so it can be tested directly and reasoned about in isolation.

The Event Coordinator gets no early bonus, but it does have a deadline (12h after
the request's last individual task). Finishing after it costs points on the same
late curve, measured from that deadline -- see `overdue_multiplier`.

Base points by role:
    Task Supervisor   -> 0 (a duty, not scored work)
    Event Coordinator -> coordinator_points
    Vetter            -> vetter_points (legacy: only requests already in flight
                         still have one)
    anything else     -> domain_task_points

A completion-timing modifier is applied when the task is finished, where
`turnaround_hours` is measured from the event end (Coverage) or from request
creation (Post / no event):

    <= early_window_hours    -> +early_bonus_pct%
    >  late_threshold_hours  -> -(late_penalty_pct, plus subsequent_penalty_pct
                                  for each further block of
                                  subsequent_delay_hours)
    otherwise                -> unchanged

NOTE (docs/PRD.md §10.1): the reference point for `turnaround_hours` is still
unconfirmed — it may be intended as relative to each task's own deadline
instead. This port preserves the existing behaviour exactly; changing it is a
one-line edit in workflow.complete_task, and needs a decision from the
Secretary/POC first.
"""

from __future__ import annotations

import math

from core.constants import TASK_EVENT_COORDINATOR, TASK_SUPERVISOR, TASK_VETTER


def base_points_for(task_name: str, scheme) -> int:
    if task_name == TASK_SUPERVISOR:
        return 0  # supervising is a duty, not scored work
    if task_name == TASK_EVENT_COORDINATOR:
        return scheme.coordinator_points
    if task_name == TASK_VETTER:
        return scheme.vetter_points
    return scheme.domain_task_points


def timing_multiplier(turnaround_hours: float, scheme) -> float:
    """Multiplier for a given turnaround, e.g. 1.30 for early, 0.70 for late."""
    if turnaround_hours <= scheme.early_window_hours:
        return 1 + scheme.early_bonus_pct / 100

    if turnaround_hours > scheme.late_threshold_hours:
        return _late_multiplier(turnaround_hours - scheme.late_threshold_hours, scheme)

    return 1.0


def _late_multiplier(hours_past: float, scheme) -> float:
    """
    The late-penalty curve: `late_penalty_pct` once past the line, then
    `subsequent_penalty_pct` more for each further full `subsequent_delay_hours`
    block, never below zero. `hours_past` is measured from the line itself.
    """
    # max(1, ...) guards a misconfigured zero block length.
    block = max(1, scheme.subsequent_delay_hours)
    extra_blocks = int(hours_past // block)
    penalty_pct = scheme.late_penalty_pct + scheme.subsequent_penalty_pct * extra_blocks
    return max(0.0, 1 - penalty_pct / 100)


def overdue_multiplier(hours_overdue: float, scheme) -> float:
    """
    Multiplier for a task that has its own deadline and was finished after it
    (the Event Coordinator): 1.0 on time, then the same late curve a deliverable
    gets, measured from the deadline. It never earns an early bonus.
    """
    if hours_overdue <= 0:
        return 1.0
    return _late_multiplier(hours_overdue, scheme)


def final_points(base: int, turnaround_hours: float, scheme) -> int:
    """
    Base points adjusted for completion timing.

    Uses `floor(x + 0.5)` rather than Python's `round`, which does banker's
    rounding and would quietly disagree with the JavaScript original on exact
    .5 values — awarding 12 points where the old system awarded 13.
    """
    exact = base * timing_multiplier(turnaround_hours, scheme)
    return math.floor(exact + 0.5)
