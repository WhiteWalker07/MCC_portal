"""
Shared literals for the portal.

These strings are load-bearing: they are stored in the database, compared in the
engine, and rendered in templates. They are collected here so a rename is one
edit rather than a grep. Values are kept byte-identical to the Express/Mongo
implementation they were ported from (`server/src/types.ts` and the status
strings scattered through `server/src/services/workflow.ts`) so behaviour and
any exported data line up exactly.
"""


class RequestType:
    COVERAGE = "Coverage"
    POST = "Post"

    CHOICES = [(COVERAGE, "Coverage"), (POST, "Post")]


class RequestStatus:
    """
    Request lifecycle (docs/PRD.md §6).

        New -> (Pending for POC approval) -> Request Accepted
            -> Event Covered -> Ready To post -> Posted

    `Rejected` is terminal. The odd capitalisation of "Ready To post" is
    deliberate — it matches the stored values of the system this replaced.
    """

    NEW = "New"
    PENDING = "Pending for POC approval"
    ACCEPTED = "Request Accepted"
    EVENT_COVERED = "Event Covered"
    READY_TO_POST = "Ready To post"
    POSTED = "Posted"
    REJECTED = "Rejected"

    CHOICES = [
        (NEW, NEW),
        (PENDING, PENDING),
        (ACCEPTED, ACCEPTED),
        (EVENT_COVERED, EVENT_COVERED),
        (READY_TO_POST, READY_TO_POST),
        (POSTED, POSTED),
        (REJECTED, REJECTED),
    ]

    #: States in which a newly-added or reassigned task should land as CONFIRMED
    #: rather than PROPOSED-awaiting-approval — the request itself has already
    #: cleared that gate. (`CONFIRMED_STATES` in the old engine/assignment.ts.)
    #: Points are a separate concern, earned on task completion regardless of
    #: request status — see engine/workflow.py's `_award_completion_points`.
    CONFIRMED_STATES = frozenset({ACCEPTED, EVENT_COVERED, READY_TO_POST, POSTED})

    #: Requests that can no longer be acted on from the Assignments view.
    TERMINAL = frozenset({POSTED, REJECTED})


class TaskStatus:
    """
    Task lifecycle (docs/PRD.md §5.4).

        PROPOSED -> CONFIRMED -> DONE
                    CONFIRMED -> LATE  (deadline passed; still completable)

    UNFILLED means no eligible member was found. SCHEDULED is only used by the
    generated per-platform Post tasks.
    """

    PROPOSED = "PROPOSED"
    CONFIRMED = "CONFIRMED"
    DONE = "DONE"
    LATE = "LATE"
    UNFILLED = "UNFILLED"
    SCHEDULED = "SCHEDULED"

    CHOICES = [
        (PROPOSED, PROPOSED),
        (CONFIRMED, CONFIRMED),
        (DONE, DONE),
        (LATE, LATE),
        (UNFILLED, UNFILLED),
        (SCHEDULED, SCHEDULED),
    ]

    #: A task may only be marked done from these.
    COMPLETABLE = frozenset({CONFIRMED, LATE})


class Availability:
    AVAILABLE = "available"
    OUT = "out"

    CHOICES = [(AVAILABLE, "On work"), (OUT, "Out of work")]


class LeaveStatus:
    """
    A member's request to go Out of work (docs: USER-GUIDE). PENDING until the
    POC/Admin decides; APPROVED means it will start (or has started) on its start
    date; ENDED once the member is back on work, by the end date or by hand.
    """

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    ENDED = "ended"

    CHOICES = [
        (PENDING, "Waiting for approval"),
        (APPROVED, "Approved"),
        (REJECTED, "Declined"),
        (CANCELLED, "Withdrawn"),
        (ENDED, "Ended"),
    ]

    #: A member may have only one request in these states at a time.
    OPEN = frozenset({PENDING, APPROVED})


#: The four production verticals. A member has a primary and an optional
#: secondary one; a domain head's scope covers members in either. "" = unscoped.
VERTICALS = ["Photography", "Videography", "Graphic Designs", "Content Writing"]

#: The skills a member gets for working in a vertical. Used to derive the
#: skills of members seeded from a (primary, secondary) vertical pair.
VERTICAL_SKILLS = {
    "Photography": ["Photography", "Photo Editing"],
    "Videography": ["Videography", "Video Editing"],
    "Graphic Designs": ["Graphic design"],
    "Content Writing": ["Content Writing"],
}

_VERTICAL_ALIASES = {v.lower(): v for v in VERTICALS} | {"graphic design": "Graphic Designs"}


def normalise_vertical(text: str) -> str:
    """
    Map the spellings people actually type ("Graphic Design", "content writing")
    onto the canonical vertical names. Anything unrecognised comes back trimmed
    but otherwise untouched, so a genuinely new vertical isn't silently dropped.
    """
    cleaned = (text or "").strip()
    return _VERTICAL_ALIASES.get(cleaned.lower(), cleaned)


CAMPUS_MBA = "MBA Campus"
CAMPUSES = [CAMPUS_MBA, "BMS Campus"]

COMMITTEE_TYPES = ["Club", "Committee", "SIG", "Office"]


#: Named tasks the engine special-cases. Everything else is a plain deliverable.
TASK_EVENT_COORDINATOR = "Event Coordinator"
TASK_VETTER = "Vetter"
TASK_POST = "Post"
TASK_GRAPHIC_DESIGNER = "Graphic Designer"
TASK_CONTENT_WRITER = "Content Writer"

#: A 2nd-year who oversees one Coverage request. It is the only role a
#: 2nd-year is ever given (and only staff may change who holds it); it closes
#: on its own when the Event Coordinator finishes, earns no points and has no
#: deadline.
TASK_SUPERVISOR = "Task Supervisor"

#: Strikes come in two colours, both given by hand. A yellow is a warning that a
#: vertical head, the POC or an admin can give; a red is serious and reserved to
#: the POC/admin. Neither blocks assignment or affects the engine in any way.
STRIKE_YELLOW = "yellow"
STRIKE_RED = "red"
STRIKE_CHOICES = [(STRIKE_YELLOW, "Yellow (warning)"), (STRIKE_RED, "Red (serious)")]

#: A club may change an event's time, or its sub-events, only while the event
#: is still more than this many hours away.
EDIT_CUTOFF_HOURS = 24

#: The Event Coordinator is due this many hours after the last deadline of the
#: request's other tasks (or after the event end if it has none). Finishing
#: later costs points on the same curve as a late deliverable.
COORDINATOR_GRACE_HOURS = 12

#: Coverage requests derive an editor task from each shoot role (docs/PRD.md
#: §5.2). The editor is the shooter themself: each photographer edits their own
#: photos and each videographer their own footage.
DERIVED_EDITOR = {
    "Photographer": "Photo Editor",
    "Videographer": "Video Editor",
}


class Attendance:
    """How an invitee's attendance at a team meeting was marked."""

    UNMARKED = ""
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"
    EXCUSED = "excused"

    CHOICES = [
        (UNMARKED, "Not marked"),
        (PRESENT, "Present"),
        (LATE, "Late"),
        (ABSENT, "Absent"),
        (EXCUSED, "Excused"),
    ]

#: Roles that appear on the roster emailed to the requesting committee — the
#: people they will actually meet on the day.
ROSTER_ROLES = frozenset(
    {"Event Coordinator", "Photographer", "Videographer", "Content Writer"}
)

HOUR = 3600.0
DAY = 86400.0
