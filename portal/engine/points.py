"""
Scoring scheme (docs/PRD.md §5.7).

Ported verbatim from `server/src/engine/points.ts`. Pure arithmetic — no
database access — so it can be tested directly and reasoned about in isolation.

Base points by role:
    Event Coordinator -> coordinator_points
    Vetter            -> vetter_points
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

from core.constants import TASK_EVENT_COORDINATOR, TASK_VETTER


def base_points_for(task_name: str, scheme) -> int:
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
        # Each further `subsequent_delay_hours` block past the threshold adds
        # another penalty step. max(1, ...) guards a misconfigured zero.
        block = max(1, scheme.subsequent_delay_hours)
        extra_blocks = int((turnaround_hours - scheme.late_threshold_hours) // block)
        penalty_pct = scheme.late_penalty_pct + scheme.subsequent_penalty_pct * extra_blocks
        return max(0.0, 1 - penalty_pct / 100)

    return 1.0


def final_points(base: int, turnaround_hours: float, scheme) -> int:
    """
    Base points adjusted for completion timing.

    Uses `floor(x + 0.5)` rather than Python's `round`, which does banker's
    rounding and would quietly disagree with the JavaScript original on exact
    .5 values — awarding 12 points where the old system awarded 13.
    """
    exact = base * timing_multiplier(turnaround_hours, scheme)
    return math.floor(exact + 0.5)
