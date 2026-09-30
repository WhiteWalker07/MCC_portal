"""
Portal data model.

Ported from the six MongoDB collections described in docs/PRD.md §9. Two
structural changes were made rather than transliterating the document shapes:

1. The `config` collection held five key-addressed documents (`settings`,
   `taskTypes`, `slots`, `platforms`, `points`). In a relational database those
   become real tables — which also means Django admin gives you a complete
   editor for the engine's tunables for free.

2. Requests and tasks are joined by a real foreign key instead of a hex string,
   so cascading deletes and `select_related` work.

What deliberately did NOT change: admin/secretary rights stay *data* on
PortalSettings, never code, so granting them is an edit and never a deploy
(docs/PRD.md §4). And the engine-owned idempotency flags on Task
(`points_awarded`, `timing_applied`, `struck`) are preserved exactly — the
workflow's "apply this once and only once" guarantees hang off them.
"""

from __future__ import annotations

from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from .constants import (
    Attendance,
    Availability,
    RequestStatus,
    RequestType,
    TaskStatus,
)


class SingletonModel(models.Model):
    """
    A one-row table. `load()` returns that row, creating it with field defaults
    if it isn't there yet, so callers never have to handle its absence.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):  # pragma: no cover - guarded by admin too
        raise NotImplementedError(f"{type(self).__name__} is a singleton and cannot be deleted.")

    @classmethod
    def load(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


# ── Configuration ────────────────────────────────────────────────────────────


class PortalSettings(SingletonModel):
    """Engine-wide tunables. Was `config/settings`."""

    sla_hours = models.PositiveIntegerField(
        default=48,
        help_text="Coverage requests for events starting within this many hours are held for POC approval.",
    )
    campus_strict = models.BooleanField(
        default=True,
        help_text="Only assign members from the same campus as the requesting body.",
    )
    require_approval_always = models.BooleanField(
        default=False,
        help_text="Hold every request for POC approval, not just short-notice ones.",
    )

    secretary_emails = models.JSONField(
        default=list,
        blank=True,
        help_text="Secretary / POC accounts. Approve or reject gated requests, manage the roster.",
    )
    admin_emails = models.JSONField(
        default=list,
        blank=True,
        help_text="Admin accounts. Everything a secretary can do, plus editing the point scheme.",
    )
    allowed_domains = models.JSONField(
        default=list,
        blank=True,
        help_text="Email domains permitted to sign in, e.g. [\"iimsirmaur.ac.in\"].",
    )

    head_email = models.EmailField(blank=True, help_text="Copied on late-task notifications.")
    committee_name = models.CharField(max_length=200, default="Media & Communications Committee")
    default_acronym = models.CharField(
        max_length=20,
        default="MEDIA",
        help_text="Reference-code prefix for requests from accounts that aren't a registered committee.",
    )
    general_seq = models.PositiveIntegerField(
        default=0,
        help_text="Counter behind MEDIA_n reference codes. Managed by the engine — don't edit.",
    )

    class Meta:
        verbose_name = "portal settings"
        verbose_name_plural = "portal settings"

    def __str__(self) -> str:
        return "Portal settings"


class PointsScheme(SingletonModel):
    """
    Scoring scheme. Was `config/points`.

    Base points by role, plus the completion-timing modifier applied when a task
    is finished: a bonus inside the early window, an escalating penalty past the
    late threshold. Full formula in engine/points.py (docs/PRD.md §5.7).
    """

    coordinator_points = models.IntegerField(default=20)
    domain_task_points = models.IntegerField(default=10)
    vetter_points = models.IntegerField(default=10)

    early_window_hours = models.IntegerField(
        default=24, help_text="Finish within this many hours to earn the early bonus."
    )
    early_bonus_pct = models.IntegerField(default=30)
    late_threshold_hours = models.IntegerField(
        default=48, help_text="Past this many hours, the late penalty starts."
    )
    late_penalty_pct = models.IntegerField(default=30)
    subsequent_delay_hours = models.IntegerField(
        default=6, help_text="Each further block of this many hours adds another penalty step."
    )
    subsequent_penalty_pct = models.IntegerField(default=10)

    class Meta:
        verbose_name = "point scheme"
        verbose_name_plural = "point scheme"

    def __str__(self) -> str:
        return "Point scheme"


class TaskType(models.Model):
    """One row per assignable role. Was an entry in `config/taskTypes`."""

    task = models.CharField(max_length=100, unique=True)
    required_skill = models.CharField(
        max_length=100,
        blank=True,
        help_text="A member needs this skill to be eligible. Blank = no skill needed.",
    )
    points = models.IntegerField(default=0)
    sla_hours = models.PositiveIntegerField(
        default=0,
        help_text="Hours after the event ends (or after submission) that this task is due. 0 = due at event end.",
    )
    at_event = models.BooleanField(
        default=False,
        help_text="Performed during the event itself, so the assignee's calendar is checked for clashes.",
    )
    requestable = models.BooleanField(
        default=False, help_text="A committee can ask for this role on the New Request form."
    )
    internal_assignable = models.BooleanField(
        default=False, help_text="Staff can add this task to a request manually."
    )
    vertical = models.CharField(
        max_length=50, blank=True, help_text="Scopes which domain head may assign it. Blank = unscoped."
    )

    class Meta:
        ordering = ["task"]

    def __str__(self) -> str:
        return self.task


class PostSlot(models.Model):
    """A daily publishing slot in IST wall-clock time. Was `config/slots`."""

    time = models.TimeField(unique=True)

    class Meta:
        ordering = ["time"]

    def __str__(self) -> str:
        return self.time.strftime("%H:%M")


class Platform(models.Model):
    """A social channel and who posts to it. Was an entry in `config/platforms`."""

    platform = models.CharField(max_length=50)
    handler_email = models.EmailField(
        help_text="Who publishes to this channel. Scheduled posts are assigned to them."
    )
    points = models.IntegerField(default=0)
    active = models.BooleanField(default=True)

    class Meta:
        ordering = ["platform", "handler_email"]
        constraints = [
            models.UniqueConstraint(
                fields=["platform", "handler_email"], name="unique_platform_handler"
            )
        ]

    def save(self, *args, **kwargs):
        # Assignee lookups everywhere compare lower-cased emails; a handler typed
        # in mixed case in the admin would get posts they can never find.
        self.handler_email = (self.handler_email or "").strip().lower()
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.platform} ({self.handler_email})"


# ── People and requesting bodies ─────────────────────────────────────────────


class Committee(models.Model):
    """
    A requesting body — club, committee, SIG or office. Keyed by the shared login
    email its members use. Being in this table is what allows an account to raise
    a Coverage request (docs/PRD.md §5.1).
    """

    email = models.EmailField(unique=True, help_text="Shared login address for this body.")
    name = models.CharField(max_length=200)
    acronym = models.CharField(max_length=20, help_text="Prefix for this body's reference codes.")
    type = models.CharField(max_length=30, default="Committee")
    campus = models.CharField(max_length=50, blank=True)
    last_seq = models.PositiveIntegerField(
        default=0, help_text="Reference-code counter. Managed by the engine — don't edit."
    )
    logo = models.CharField(
        max_length=300, blank=True, help_text="Filename under static/logos/, or a full URL."
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} ({self.acronym})"

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        self.acronym = self.acronym.strip().upper()
        super().save(*args, **kwargs)


class TeamMember(models.Model):
    """
    A member of the media team. Keyed by email so it lines up with the Google
    account they sign in with; there is no foreign key to auth.User because a
    member exists in the roster before they ever log in.
    """

    email = models.EmailField(unique=True)
    name = models.CharField(max_length=200)
    skills = models.JSONField(
        default=list, blank=True, help_text="Matched against a task type's required skill."
    )
    vertical = models.CharField(
        max_length=50, blank=True, help_text="Primary vertical. Auto-assignment tries these members first."
    )
    secondary_vertical = models.CharField(
        max_length=50,
        blank=True,
        help_text="Second vertical they can also work in. Tried after the primary-vertical members.",
    )
    year = models.PositiveSmallIntegerField(
        default=1,
        help_text="Academic year. Second-years are only ever Task Supervisors; first-years do the work.",
    )
    campus = models.CharField(max_length=50, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    active = models.BooleanField(default=True)

    points = models.IntegerField(default=0)
    yellow_strikes = models.IntegerField(default=0, help_text="Warnings. Given manually; never block work.")
    red_strikes = models.IntegerField(default=0, help_text="Serious strikes. Given manually by POC/Admin.")

    domain_head_of = models.CharField(
        max_length=50,
        blank=True,
        help_text="Vertical this member heads, granting manual-assign rights within it. Blank = not a head.",
    )

    availability = models.CharField(
        max_length=20, choices=Availability.CHOICES, default=Availability.AVAILABLE
    )
    availability_changed_at = models.DateTimeField(
        default=timezone.now, help_text="When the current availability state began."
    )
    on_work_days = models.FloatField(
        default=0.0, validators=[MinValueValidator(0.0)], help_text="Banked days on work."
    )
    out_days = models.FloatField(
        default=0.0, validators=[MinValueValidator(0.0)], help_text="Banked days out of work."
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"

    def save(self, *args, **kwargs):
        self.email = self.email.strip().lower()
        super().save(*args, **kwargs)

    @property
    def is_second_year(self) -> bool:
        return self.year == 2

    def in_vertical(self, vertical: str) -> bool:
        """True if `vertical` is this member's primary or secondary vertical."""
        return bool(vertical) and vertical in (self.vertical, self.secondary_vertical)

    @property
    def vertical_label(self) -> str:
        """"Photography / Videography", or just the one, or blank."""
        return " / ".join(v for v in (self.vertical, self.secondary_vertical) if v)

    @property
    def strike_label(self) -> str:
        return f"{self.yellow_strikes} yellow, {self.red_strikes} red"


# ── Workflow ─────────────────────────────────────────────────────────────────


class Request(models.Model):
    """
    One Coverage or Post submission. Was the `requests` collection.

    Fields the client is never allowed to set — status, ref_code, campus,
    coordinator_email, roster, posts — are owned by the engine. The forms in
    ui/ simply don't expose them; the views don't read them from POST data.
    """

    ref_code = models.CharField(
        max_length=50, blank=True, db_index=True, help_text="Human-readable ID, e.g. SPT_12."
    )
    type = models.CharField(max_length=20, choices=RequestType.CHOICES)
    status = models.CharField(
        max_length=40, choices=RequestStatus.CHOICES, default=RequestStatus.NEW, db_index=True
    )

    event_name = models.CharField(max_length=300)
    event_start = models.DateTimeField(null=True, blank=True)
    event_end = models.DateTimeField(null=True, blank=True)
    venue = models.CharField(max_length=300, blank=True)

    requester = models.CharField(max_length=200, blank=True, help_text="Person raising it, free text.")
    contact_email = models.EmailField(db_index=True, help_text="The signed-in account that submitted it.")
    campus = models.CharField(max_length=50, blank=True)

    roles_needed = models.JSONField(default=list, blank=True)
    platforms = models.JSONField(default=list, blank=True)
    content_links = models.TextField(blank=True)
    notes = models.TextField(blank=True)

    coordinator_email = models.EmailField(blank=True, db_index=True)
    supervisor_email = models.EmailField(
        blank=True, db_index=True, help_text="The Task Supervisor (Coverage only). Only staff can change it."
    )
    roster = models.JSONField(
        default=list, blank=True, help_text="Contact-facing assignees, as sent to the requester."
    )
    posts = models.JSONField(default=list, blank=True, help_text="Per-platform scheduling outcome.")

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    decision_by = models.EmailField(blank=True)
    decision_at = models.DateTimeField(null=True, blank=True)
    reject_reason = models.TextField(blank=True)
    ready_by = models.EmailField(blank=True)
    ready_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.ref_code or f'#{self.pk}'} — {self.event_name}"

    @property
    def is_terminal(self) -> bool:
        return self.status in RequestStatus.TERMINAL


class SubEvent(models.Model):
    """
    One item in a Coverage request's schedule — "Inauguration, 10:00, Auditorium".

    A sub-event is information for the team, not extra work: the main event's
    team covers all of them, so it has no tasks or assignees of its own. It may
    fall outside the main event's window.
    """

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="sub_events")
    name = models.CharField(max_length=200)
    start = models.DateTimeField()
    end = models.DateTimeField()
    venue = models.CharField(max_length=300, blank=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["start", "pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end__gt=models.F("start")), name="subevent_end_after_start")
        ]

    def __str__(self) -> str:
        return f"{self.name} ({self.request})"


class Task(models.Model):
    """
    One unit of assigned work on a request. Was the `tasks` collection.

    Event details (`event_name`, `event_start`, `event_end`, `venue`) and
    `ref_code` are denormalised onto the task, exactly as the Mongo version had
    them. That is intentional: My Tasks and the deadline sweep both read tasks
    without their parent, and the values are immutable once assigned.
    """

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="tasks")
    req_type = models.CharField(max_length=20)
    ref_code = models.CharField(max_length=50, blank=True, db_index=True)

    task = models.CharField(max_length=100)
    required_skill = models.CharField(max_length=100, blank=True)
    at_event = models.BooleanField(default=False)
    vertical = models.CharField(max_length=50, blank=True, db_index=True)
    platform = models.CharField(max_length=50, blank=True)

    member = models.CharField(max_length=200, blank=True, help_text="Assignee's display name.")
    email = models.EmailField(blank=True, db_index=True, help_text="Assignee.")
    phone = models.CharField(max_length=30, blank=True)

    points = models.IntegerField(default=0)
    points_awarded = models.BooleanField(
        default=False, help_text="Engine flag: base points already credited. Don't edit."
    )
    timing_applied = models.BooleanField(
        default=False, help_text="Engine flag: completion-timing modifier already applied. Don't edit."
    )
    struck = models.BooleanField(
        default=False,
        help_text="Engine flag: this task was already marked LATE by the deadline sweep. Don't edit.",
    )

    status = models.CharField(
        max_length=20, choices=TaskStatus.CHOICES, default=TaskStatus.PROPOSED, db_index=True
    )
    reason = models.CharField(
        max_length=300, blank=True, help_text="Why this task is UNFILLED, when it is."
    )

    deadline = models.DateTimeField(null=True, blank=True, db_index=True)
    scheduled_at = models.DateTimeField(null=True, blank=True, help_text="Post tasks only.")
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    coordinator_email = models.EmailField(blank=True, db_index=True)
    event_name = models.CharField(max_length=300, blank=True)
    event_start = models.DateTimeField(null=True, blank=True)
    event_end = models.DateTimeField(null=True, blank=True)
    venue = models.CharField(max_length=300, blank=True)

    paired_task = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="paired_editors",
        help_text="On an editing task: the shooter task whose work it edits. "
        "The editor follows the shooter when the shooter is reassigned.",
    )

    class Meta:
        ordering = ["deadline", "task"]
        indexes = [models.Index(fields=["status", "deadline"])]

    def __str__(self) -> str:
        return f"{self.ref_code} {self.task} → {self.email or 'UNFILLED'}"

    @property
    def is_completable(self) -> bool:
        """Open for its assignee to mark done, ignoring the event-start guard."""
        return self.status in TaskStatus.COMPLETABLE

    @property
    def awaits_event(self) -> bool:
        """
        True when the event this task covers hasn't started yet. You can't have
        covered an event that hasn't happened (docs/PRD.md §5.4).
        """
        return bool(self.event_start and self.event_start > timezone.now())


class Meeting(models.Model):
    """
    A team meeting called by the POC, an admin or a vertical head. Invitees are
    stored as `MeetingInvite` rows, so who was called, and how they were marked
    afterwards, is a permanent record.
    """

    title = models.CharField(max_length=200)
    start = models.DateTimeField()
    end = models.DateTimeField()
    venue = models.CharField(max_length=300, blank=True, help_text="May be blank if the MOM person is booking it.")
    agenda = models.TextField(blank=True)

    called_by = models.EmailField(db_index=True, help_text="Who called the meeting.")
    wants_mom = models.BooleanField(
        default=False, help_text="The caller asked for someone to take minutes and book the venue."
    )
    mom_email = models.EmailField(
        blank=True, help_text="Who takes the minutes and books the venue. Responsibility only: no points."
    )

    cancelled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-start", "-pk"]
        constraints = [
            models.CheckConstraint(condition=models.Q(end__gt=models.F("start")), name="meeting_end_after_start")
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.start:%Y-%m-%d %H:%M})"

    @property
    def is_cancelled(self) -> bool:
        return self.cancelled_at is not None

    @property
    def has_started(self) -> bool:
        return self.start <= timezone.now()

    @property
    def thread_key(self) -> str:
        """Stable reference used to thread every email about this meeting."""
        return f"MEET_{self.pk}"


class MeetingInvite(models.Model):
    """One invitee of a meeting, and how the caller marked their attendance."""

    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name="invites")
    member = models.ForeignKey(TeamMember, on_delete=models.CASCADE, related_name="meeting_invites")
    attendance = models.CharField(max_length=10, choices=Attendance.CHOICES, blank=True, default=Attendance.UNMARKED)
    strike_given = models.BooleanField(
        default=False,
        help_text="Engine flag: marking this person Absent gave them a yellow strike. Don't edit.",
    )
    marked_by = models.EmailField(blank=True)
    marked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["member__name"]
        constraints = [
            models.UniqueConstraint(fields=["meeting", "member"], name="unique_invite_per_meeting")
        ]

    def __str__(self) -> str:
        return f"{self.member.name} @ {self.meeting.title}"


class ActivityLog(models.Model):
    """Append-only audit trail. Was the `activityLog` collection."""

    timestamp = models.DateTimeField(default=timezone.now, db_index=True)
    event = models.CharField(max_length=50, db_index=True)
    request = models.ForeignKey(
        Request, on_delete=models.SET_NULL, null=True, blank=True, related_name="activity"
    )
    ref_code = models.CharField(max_length=50, blank=True)
    actor = models.CharField(max_length=200, blank=True, help_text="Who caused it, or 'engine'.")
    member = models.CharField(max_length=200, blank=True, help_text="Who it happened to.")
    detail = models.CharField(max_length=500, blank=True)

    class Meta:
        ordering = ["-timestamp"]
        verbose_name = "activity log entry"
        verbose_name_plural = "activity log"

    def __str__(self) -> str:
        return f"[{self.event}] {self.ref_code} {self.detail}"
