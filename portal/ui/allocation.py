"""
The "choose the team" step for a request a POC/Admin is entering on a club's behalf.

Nothing is saved, and nobody is emailed, until the staff member presses
"Save and send": they fill the form, press Next, see the team the engine would pick
for each task (each with a dropdown to change it), and only then confirm. This
module builds that page's rows and checks the people chosen by hand; the view in
`views.request_new` carries the form through the two steps.

A hand pick is checked with the same rules as reassigning a task afterwards
(`engine.assignment.validate_member`): active, not Out of work, a first-year for
hands-on roles and a second-year for the supervisor, the right campus, and free
during the event. Anything not picked is left to the engine.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.utils import timezone

from core.config import get_points_scheme, get_settings, get_task_types, get_team
from engine.assign import eligible_members
from engine.assignment import validate_member
from engine.pipeline import build_pipeline
from engine.workflow import Staffed, propose_team
from services.calendar import calendar_service

#: The form-field name for the dropdown of a task: `pick:<Task Name>`.
PICK_PREFIX = "pick:"

#: Fields of the posted form that belong to this step, not to the request itself.
STEP_FIELDS = {"csrfmiddlewaretoken", "step"}


class _RememberingCalendar:
    """
    Asks the real calendar once per person and time window, then remembers. Every
    task of an event shares one window, so without this the page would query each
    person's calendar once per task (a dozen times over) as it builds the dropdowns.
    """

    def __init__(self, inner):
        self._inner = inner
        self._answers: dict = {}

    def is_free(self, email, start, end):
        key = (email.lower(), start, end)
        if key not in self._answers:
            self._answers[key] = self._inner.is_free(email, start, end)
        return self._answers[key]


@dataclass
class Row:
    """One line of the allocation page: a task, its candidates, and who is selected."""

    task: str
    vertical: str
    due: object
    options: list  # [(email, label)]
    selected: str  # email, or "" for "let the system decide"
    follows: str = ""  # for an editing task: the shoot it follows ("Photographer")
    note: str = ""  # why nobody was suggested
    warnings: list = field(default_factory=list)
    error: str = ""


def _label(member) -> str:
    verticals = member.vertical_label
    return f"{member.name}" + (f" · {verticals}" if verticals else "") + f" · {member.points or 0} pts"


def pipeline_for(draft):
    """The tasks this request will have (they depend on its type and the roles ticked)."""
    return build_pipeline(draft, get_task_types(), timezone.now(), get_points_scheme())


def picks_from_post(post, task_names) -> dict[str, str]:
    """{task name: email} for every dropdown the staff member set (blank = left to the system)."""
    picks = {}
    for name in task_names:
        value = (post.get(f"{PICK_PREFIX}{name}") or "").strip().lower()
        if value:
            picks[name] = value
    return picks


def carried_fields(post) -> list[tuple[str, str]]:
    """Every field of the first-step form, to be re-posted unchanged with the confirmation."""
    out = []
    for key in post:
        if key in STEP_FIELDS or key.startswith(PICK_PREFIX):
            continue
        out.extend((key, value) for value in post.getlist(key))
    return out


def validate_picks(draft, picks: dict[str, str], pipeline):
    """
    Check each hand pick against the real rules. Returns (preferred, errors):
    `preferred` maps task name -> TeamMember for the valid ones, `errors` maps
    task name -> a sentence saying what is wrong with the rest.
    """
    settings = get_settings()
    by_name = {p.task: p for p in pipeline}
    preferred, errors = {}, {}
    for name, email in picks.items():
        task = by_name.get(name)
        if task is None:
            continue  # a stale field for a role this request doesn't have
        result = validate_member(
            email, task.required_skill, task.at_event, draft, settings,
            require_skill=False, task_name=name,
        )
        if result.ok:
            preferred[name] = result.member
        else:
            errors[name] = result.reason
    return preferred, errors


def build_rows(draft, posted_picks: dict[str, str] | None = None, errors: dict[str, str] | None = None):
    """
    The rows for the allocation page. With no `posted_picks` the selected person in
    each row is the engine's suggestion; with them (the page being shown again after
    a mistake) it is what the staff member had chosen.
    """
    errors = errors or {}
    posted_picks = posted_picks or {}
    calendar = _RememberingCalendar(calendar_service())
    settings = get_settings()
    team = get_team()

    pipeline = pipeline_for(draft)

    # Valid hand picks are applied so the suggestions for the other roles steer
    # around them, exactly as they will when the request is saved.
    preferred, _ = validate_picks(draft, posted_picks, pipeline)
    staffed: list[Staffed] = propose_team(draft, preferred)

    rows = []
    holders_by_task = {s.pipeline_task.task: s.member for s in staffed}
    for item in staffed:
        task = item.pipeline_task
        # An editing task follows its shoot only if the shoot has someone to follow.
        follows = task.pairs_with if holders_by_task.get(task.pairs_with) is not None else ""
        pool = eligible_members(
            task.required_skill, task.at_event, draft, settings, team, calendar,
            require_skill=False, task_name=task.task, vertical=task.vertical,
        )
        chosen = posted_picks.get(task.task)
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
        rows.append(
            Row(
                task=task.task,
                vertical=task.vertical,
                due=task.deadline,
                options=options,
                selected=selected,
                follows=follows,
                note="" if item.member else (item.reason or "nobody eligible"),
                error=errors.get(task.task, ""),
            )
        )

    # Warn (never block) when one person ends up with two on-site roles.
    holders: dict[str, list[str]] = {}
    for item in staffed:
        if item.member is not None and item.pipeline_task.at_event:
            holders.setdefault(item.member.email, []).append(item.pipeline_task.task)
    for row, item in zip(rows, staffed):
        if item.member is not None and len(holders.get(item.member.email, [])) > 1:
            others = [t for t in holders[item.member.email] if t != row.task]
            row.warnings.append(f"{item.member.name} is also doing {', '.join(others)} at the event.")
    return rows
