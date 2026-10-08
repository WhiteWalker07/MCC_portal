"""
The "choose the team" step for a request a POC/Admin is entering on a club's behalf.

Nothing is saved, and nobody is emailed, until the staff member presses
"Save and send": they fill the form, press Next, see the team the engine would pick
for each task (each with a dropdown to change it), and only then confirm. This
module builds that page and checks the people chosen by hand; the view in
`views.request_new` carries the form through the two steps.

A multi-day event with sub-events has its own photographer/videographer (and their
editing) for every sub-event, so the page groups tasks by sub-event. Beside the
tasks the system proposes, staff can add more with "Add a task" (for example a
second photographer for one sub-event).

A hand pick is checked with the same rules as reassigning a task afterwards
(`engine.assignment.validate_member`): active, not Out of work, a first-year for
hands-on roles and a second-year for the supervisor, the right campus, and free
during that task's own time. Anything not picked is left to the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from core.config import get_points_scheme, get_settings, get_task_types, get_team
from core.constants import TASK_EVENT_COORDINATOR, TASK_SUPERVISOR
from engine.assign import eligible_members
from engine.assignment import validate_member
from engine.pipeline import build_pipeline, number_extras, ordered_sub_events, task_key
from engine.workflow import Staffed, staff_pipeline
from services.calendar import RememberingCalendar, calendar_service

#: The form-field name for the dropdown of a task: `pick:<key>`, where the key is the
#: task's name, plus `@s<n>` for the nth sub-event and `+<n>` for an extra one.
PICK_PREFIX = "pick:"
#: Fields of the "Add a task" rows: `extra-TOTAL`, `extra-<i>-task`, `-sub`, `-who`.
EXTRA_PREFIX = "extra-"
MAX_EXTRAS = 40

#: Fields of the posted form that belong to this step, not to the request itself.
STEP_FIELDS = {"csrfmiddlewaretoken", "step"}


@dataclass
class Row:
    """One line of the allocation page: a task, its candidates, and who is selected."""

    key: str
    task: str
    vertical: str
    due: object
    options: list  # [(email, label)]
    selected: str  # email, or "" for "let the system decide"
    follows: str = ""  # for an editing task: the shoot it follows ("Photographer")
    note: str = ""  # why nobody was suggested
    warnings: list = field(default_factory=list)
    error: str = ""


@dataclass
class ExtraRow:
    """One "Add a task" line: which task, for which part of the event, and who."""

    index: int
    task: str = ""
    sub: str = ""  # sub-event number as text, or "" for the whole event
    who: str = ""
    error: str = ""


@dataclass
class Group:
    """A heading and its rows: one sub-event, or the whole event."""

    title: str
    detail: str
    rows: list


def _label(member) -> str:
    verticals = member.vertical_label
    return f"{member.name}" + (f" · {verticals}" if verticals else "") + f" · {member.points or 0} pts"


def draft_sub_events(subevents_formset) -> list:
    """The not-yet-saved sub-events the staff member filled in, in the engine's order."""
    subs = []
    for form in subevents_formset.forms:
        if getattr(form, "cleaned_data", None) and form.has_changed():
            subs.append(form.instance)
    return ordered_sub_events(subs)


def extra_task_names() -> list[str]:
    """Task types that can be added by hand: anything assignable except the one-per-request roles."""
    return [
        t.task
        for t in get_task_types()
        if t.internal_assignable and t.task not in (TASK_EVENT_COORDINATOR, TASK_SUPERVISOR)
    ]


def extras_from_post(post, sub_count: int) -> tuple[list[ExtraRow], dict[int, str]]:
    """
    The "Add a task" rows that were filled in, and an error for any that name a task
    that can't be added. A row with no task chosen is just an unused blank row.
    """
    try:
        total = max(0, min(int(post.get(f"{EXTRA_PREFIX}TOTAL", "0") or 0), MAX_EXTRAS))
    except ValueError:
        total = 0
    valid = set(extra_task_names())
    rows, errors = [], {}
    for i in range(total):
        task = (post.get(f"{EXTRA_PREFIX}{i}-task") or "").strip()
        if not task:
            continue
        sub = (post.get(f"{EXTRA_PREFIX}{i}-sub") or "").strip()
        if not (sub.isdigit() and int(sub) < sub_count):
            sub = ""
        rows.append(
            ExtraRow(
                index=len(rows), task=task, sub=sub,
                who=(post.get(f"{EXTRA_PREFIX}{i}-who") or "").strip().lower(),
            )
        )
        if task not in valid:
            errors[len(rows) - 1] = f"{task} can't be added here."
    return rows, errors


def _extra_pairs(extra_rows) -> list[tuple[str, int | None]]:
    return [(r.task, int(r.sub) if r.sub != "" else None) for r in extra_rows]


def extra_keys(extra_rows) -> list[str]:
    """The pipeline key of each extra row, in order (the same numbering the pipeline uses)."""
    return [task_key(name, sub, n) for name, sub, n in number_extras(_extra_pairs(extra_rows))]


def pipeline_for(draft, sub_events=(), extra_rows=()):
    """The tasks this request will have: depends on its type, roles, sub-events and extras."""
    return build_pipeline(
        draft, get_task_types(), timezone.now(), get_points_scheme(),
        sub_events=ordered_sub_events(sub_events), extras=_extra_pairs(extra_rows),
    )


def picks_from_post(post, pipeline, extra_rows=()) -> dict[str, str]:
    """
    {task key: email} for every dropdown the staff member set. Blank means "let the
    system decide". The extra rows' people are folded in under the keys the pipeline
    gives those extra tasks.
    """
    picks = {}
    for task in pipeline:
        value = (post.get(f"{PICK_PREFIX}{task.ident}") or "").strip().lower()
        if value:
            picks[task.ident] = value
    for key, row in zip(extra_keys(extra_rows), extra_rows):
        if row.who:
            picks[key] = row.who
    return picks


def carried_fields(post) -> list[tuple[str, str]]:
    """Every field of the first-step form, to be re-posted unchanged with the confirmation."""
    out = []
    for key in post:
        if key in STEP_FIELDS or key.startswith((PICK_PREFIX, EXTRA_PREFIX)):
            continue
        out.extend((key, value) for value in post.getlist(key))
    return out


def validate_picks(draft, picks: dict[str, str], pipeline):
    """
    Check each hand pick against the real rules. Returns (preferred, errors):
    `preferred` maps task key -> TeamMember for the valid ones, `errors` maps
    task key -> a sentence saying what is wrong with the rest.
    """
    settings = get_settings()
    by_key = {p.ident: p for p in pipeline}
    preferred, errors = {}, {}
    for key, email in picks.items():
        task = by_key.get(key)
        if task is None:
            continue  # a stale field for a role this request doesn't have
        result = validate_member(
            email, task.required_skill, task.at_event, draft, settings,
            require_skill=False, task_name=task.task, window=task.window,
        )
        if result.ok:
            preferred[key] = result.member
        else:
            errors[key] = result.reason
    return preferred, errors


def _sub_title(sub) -> tuple[str, str]:
    start, end = timezone.localtime(sub.start), timezone.localtime(sub.end)
    detail = f"{start:%a %d %b, %H:%M}–{end:%H:%M}" + (f" · {sub.venue}" if sub.venue else "")
    return sub.name, detail


def build_page(draft, sub_events, extra_rows, posted_picks=None, errors=None, extra_errors=None):
    """
    Everything the allocation page shows: the groups of rows (one group per sub-event,
    then the whole-event tasks), the "Add a task" rows, and what those need to offer.
    With no `posted_picks` the selected person in each row is the engine's suggestion;
    with them (the page being shown again after a mistake) it is what had been chosen.
    """
    errors = errors or {}
    extra_errors = extra_errors or {}
    posted_picks = posted_picks or {}
    calendar = RememberingCalendar(calendar_service())
    settings = get_settings()
    team = get_team()
    subs = ordered_sub_events(sub_events)

    pipeline = pipeline_for(draft, subs, extra_rows)
    # Valid hand picks are applied so the suggestions for the other roles steer
    # around them, exactly as they will when the request is saved.
    preferred, _ = validate_picks(draft, posted_picks, pipeline)
    staffed: list[Staffed] = staff_pipeline(draft, pipeline, settings, team, calendar, preferred)

    holders_by_key = {s.pipeline_task.ident: s.member for s in staffed}
    shoot_name = {s.pipeline_task.ident: s.pipeline_task.task for s in staffed}
    rows_by_group: dict[int | None, list[Row]] = {}
    row_of: dict[str, Row] = {}
    for item in staffed:
        task = item.pipeline_task
        if "+" in task.ident:
            continue  # an extra task (and its editing) is shown in the "Add a task" area
        # An editing task follows its shoot only if the shoot has someone to follow.
        follows = shoot_name.get(task.pairs_with, "") if holders_by_key.get(task.pairs_with) is not None else ""
        pool = eligible_members(
            task.required_skill, task.at_event, draft, settings, team, calendar,
            require_skill=False, task_name=task.task, vertical=task.vertical, window=task.window,
        )
        chosen = posted_picks.get(task.ident)
        if chosen:
            selected = chosen
        elif follows:
            selected = ""  # an editing task follows its shoot unless set separately
        else:
            selected = item.member.email if item.member else ""
        # A hand-picked person has to be in the list even if they've since stopped
        # qualifying (so the page can still show them next to the reason).
        options = [(m.email, _label(m)) for m in pool]
        if selected and selected not in {e for e, _ in options}:
            held = next((m for m in team if m.email == selected), None)
            if held is not None:
                options.insert(0, (held.email, _label(held)))
        row = Row(
            key=task.ident, task=task.task, vertical=task.vertical, due=task.deadline,
            options=options, selected=selected, follows=follows,
            note="" if item.member else (item.reason or "nobody eligible"),
            error=errors.get(task.ident, ""),
        )
        sub_index = subs.index(task.sub_event) if task.sub_event is not None and task.sub_event in subs else None
        rows_by_group.setdefault(sub_index, []).append(row)
        row_of[task.ident] = row

    # Warn (never block) when one person has two on-site roles that overlap in time.
    on_site = [
        s for s in staffed if s.member is not None and s.pipeline_task.at_event and "+" not in s.pipeline_task.ident
    ]
    for a in on_site:
        for b in on_site:
            if a is b or a.member.email != b.member.email:
                continue
            wa = a.pipeline_task.window or (draft.event_start, draft.event_end)
            wb = b.pipeline_task.window or (draft.event_start, draft.event_end)
            if wa[0] and wb[0] and wa[0] < wb[1] and wb[0] < wa[1]:
                row_of[a.pipeline_task.ident].warnings.append(
                    f"{a.member.name} is also doing {b.pipeline_task.task} at the same time."
                )

    groups = [
        Group(*_sub_title(sub), rows=rows_by_group[i]) for i, sub in enumerate(subs) if i in rows_by_group
    ]
    if None in rows_by_group:
        groups.append(
            Group("Whole event" if subs else "", "Covers the whole request" if subs else "", rows_by_group[None])
        )

    # Extra rows: attach the error for each, and offer everyone who could take a task.
    for row in extra_rows:
        row.error = extra_errors.get(row.index) or errors.get(extra_keys(extra_rows)[row.index], "")
    everyone = eligible_members(
        "", False, draft, settings, team, calendar, require_skill=False, task_name="Photographer",
    )
    return {
        "groups": groups,
        "rows": [row for group in groups for row in group.rows],  # every task row, ungrouped
        "extra_rows": list(extra_rows),
        "blank_extra": ExtraRow(index=0),
        "extra_tasks": extra_task_names(),
        "extra_subs": [(str(i), _sub_title(s)[0]) for i, s in enumerate(subs)],
        "extra_people": [(m.email, _label(m)) for m in everyone],
    }
