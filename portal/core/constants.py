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

    #: States in which assigned work counts as confirmed, so points are awarded
    #: on assignment rather than on approval. (`CONFIRMED_STATES` in the old
    #: engine/assignment.ts.)
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


#: The four production verticals. A member's vertical scopes what a domain head
#: may assign; "" means unscoped.
VERTICALS = ["Photography", "Videography", "Graphic Designs", "Content Writing"]

CAMPUSES = ["MBA Campus", "BMS Campus"]

COMMITTEE_TYPES = ["Club", "Committee", "SIG", "Office"]


#: Named tasks the engine special-cases. Everything else is a plain deliverable.
TASK_EVENT_COORDINATOR = "Event Coordinator"
TASK_VETTER = "Vetter"
TASK_POST = "Post"

#: Coverage requests derive an editor task from each shoot role that was
#: requested (docs/PRD.md §5.2).
DERIVED_EDITOR = {
    "Photographer": "Photo Editor",
    "Videographer": "Video Editor",
}

#: Roles that appear on the roster emailed to the requesting committee — the
#: people they will actually meet on the day.
ROSTER_ROLES = frozenset(
    {"Event Coordinator", "Photographer", "Videographer", "Content Writer"}
)

HOUR = 3600.0
DAY = 86400.0
