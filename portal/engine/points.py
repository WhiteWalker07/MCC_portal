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


def _hours(value: float) -> str:
    return f"{value:g}"


def _zero_after(scheme) -> float | None:
    """
    How many hours past the late line the multiplier first reaches zero, or `None`
    if it never does (no extra penalty step and a first penalty under 100%).
    """
    if scheme.late_penalty_pct >= 100:
        return 0.0
    if scheme.subsequent_penalty_pct <= 0:
        return None
    steps = math.ceil((100 - scheme.late_penalty_pct) / scheme.subsequent_penalty_pct)
    return steps * max(1, scheme.subsequent_delay_hours)


def scheme_examples(scheme) -> dict:
    """
    What the saved scheme actually pays, as rows for the Admin page: a normal task
    at a few turnaround times, and the Event Coordinator at a few hours past its
    deadline. Built from the same functions the engine awards points with, so the
    page can never describe something the engine doesn't do.

    Each row is `(label, effect, points)`; `effect` is e.g. "+30%" or "-40%".
    """
    block = max(1, scheme.subsequent_delay_hours)
    early, late_line = scheme.early_window_hours, scheme.late_threshold_hours
    base = scheme.domain_task_points

    def effect(multiplier: float) -> str:
        pct = round((multiplier - 1) * 100)
        return "no change" if pct == 0 else f"{pct:+d}%"

    task_rows: list[tuple[str, str, int]] = []
    seen: set[float] = set()

    def add_task(label: str, hours: float) -> None:
        if hours in seen:
            return
        seen.add(hours)
        task_rows.append((label, effect(timing_multiplier(hours, scheme)), final_points(base, hours, scheme)))

    add_task(f"Within {_hours(early)} h", early)
    if early < late_line:
        add_task(f"{_hours(early)}–{_hours(late_line)} h", late_line)
    add_task(f"Just past {_hours(late_line)} h", late_line + 0.5)
    add_task(f"{_hours(late_line + block)} h", late_line + block)
    add_task(f"{_hours(late_line + 3 * block)} h", late_line + 3 * block)
    # The hour at which points reach zero. (If the very first penalty is already 100%
    # the "just past" row above says so, and there is no separate row.)
    zero = _zero_after(scheme)
    if zero:
        add_task(f"{_hours(late_line + zero)} h or more", late_line + zero)

    coordinator_rows: list[tuple[str, str, int]] = []
    seen_late: set[float] = set()

    def add_coordinator(label: str, hours_late: float) -> None:
        if hours_late in seen_late:
            return
        seen_late.add(hours_late)
        multiplier = overdue_multiplier(hours_late, scheme)
        coordinator_rows.append(
            (label, effect(multiplier), math.floor(scheme.coordinator_points * multiplier + 0.5))
        )

    add_coordinator("On time or early", 0)
    add_coordinator("Just past its deadline", 0.5)
    add_coordinator(f"{_hours(block)} h late", block)
    add_coordinator(f"{_hours(3 * block)} h late", 3 * block)
    if zero:
        add_coordinator(f"{_hours(zero)} h late or more", zero)

    return {"task_rows": task_rows, "coordinator_rows": coordinator_rows, "base": base}
