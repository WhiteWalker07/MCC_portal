"""
Forms for the portal.

These replace the hand-built markup and client-side validation in
`web/js/views/newRequest.js` and `web/js/views/admin.js`. Everything a form
accepts is a *content* field — status, reference code, campus, coordinator,
roster and points are owned by the engine and simply aren't fields here, which
is what makes "the client can't set them" structural rather than a rule someone
has to remember.
"""

from __future__ import annotations

from django import forms
from django.utils import timezone

from core.constants import (
    CAMPUSES,
    COMMITTEE_TYPES,
    STRIKE_CHOICES,
    STRIKE_YELLOW,
    VERTICALS,
    RequestType,
)
from core.models import Committee, PointsScheme, Request, SubEvent


class DateTimeLocalInput(forms.DateTimeInput):
    """Renders as <input type="datetime-local">, which browsers give a picker for."""

    input_type = "datetime-local"

    def format_value(self, value):
        if not value:
            return ""
        if isinstance(value, str):
            # A bound form being re-rendered after a validation error hands the
            # widget the raw string the browser posted (already datetime-local
            # shaped), not a parsed datetime — pass it straight through rather
            # than treating it as one.
            return value
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.strftime("%Y-%m-%dT%H:%M")


class RequestForm(forms.ModelForm):
    """
    New Coverage or Post request.

    Which fields apply depends on the type, and whether Coverage is offered at
    all depends on whether the signed-in account is a registered committee
    (docs/PRD.md §5.1) — so the field set is built per user rather than fixed.
    """

    class Meta:
        model = Request
        fields = [
            "type",
            "event_name",
            "event_start",
            "event_end",
            "venue",
            "requester",
            "roles_needed",
            "platforms",
            "content_links",
            "notes",
        ]
        widgets = {
            "event_start": DateTimeLocalInput(attrs={"class": "input"}),
            "event_end": DateTimeLocalInput(attrs={"class": "input"}),
            "notes": forms.Textarea(attrs={"rows": 3, "class": "input"}),
            "content_links": forms.Textarea(attrs={"rows": 3, "class": "input"}),
        }
        labels = {
            "event_name": "Title / event name",
            "content_links": "Content links",
            "requester": "Your name",
            "venue": "Venue",
        }
        help_texts = {
            "content_links": "Where the material lives — Drive, Dropbox, a folder link.",
        }

    def __init__(self, *args, is_committee: bool, available_roles, available_platforms, **kwargs):
        super().__init__(*args, **kwargs)
        self.is_committee = is_committee

        type_choices = list(RequestType.CHOICES) if is_committee else [
            (RequestType.POST, "Post")
        ]
        self.fields["type"] = forms.ChoiceField(
            choices=type_choices,
            initial=RequestType.POST,
            widget=forms.RadioSelect,
            label="Request type",
            help_text=(
                "Coverage books the team for an event. Post schedules content to "
                "the institute's channels."
                if is_committee
                else "Coverage requests can only be raised by a committee login."
            ),
        )

        self.fields["roles_needed"] = forms.MultipleChoiceField(
            choices=[(role, role) for role in available_roles],
            required=False,
            widget=forms.CheckboxSelectMultiple,
            label="Roles needed",
            help_text="Editors are added automatically where a shoot role needs one.",
        )
        self.fields["platforms"] = forms.MultipleChoiceField(
            choices=[(p, p) for p in available_platforms],
            required=False,
            widget=forms.CheckboxSelectMultiple,
            label="Post to",
        )

        for name in ("event_start", "event_end", "venue"):
            self.fields[name].required = False

    def clean(self):
        cleaned = super().clean()
        request_type = cleaned.get("type")

        if request_type == RequestType.COVERAGE:
            if not self.is_committee:
                raise forms.ValidationError(
                    "Coverage requests are reserved to committee accounts."
                )
            start = cleaned.get("event_start")
            end = cleaned.get("event_end")
            if not start:
                self.add_error("event_start", "When does the event start?")
            elif start < timezone.now():
                # A past event start is never intentional — it's a mistyped
                # date, not a real short-notice request — and letting it
                # through just meant it silently landed in the <48h approval
                # gate looking like a legitimate rush job (event_start being
                # before submission trivially satisfies "starts within 48h").
                self.add_error("event_start", "Event start can't be in the past — check the date.")
            if not end:
                self.add_error("event_end", "When does the event end?")
            if start and end and end <= start:
                self.add_error("event_end", "The event must end after it starts.")
            if not cleaned.get("roles_needed"):
                self.add_error("roles_needed", "Pick at least one role you need.")
        else:
            # A Post has no event; clear anything the browser sent anyway so the
            # deadline maths doesn't see a stray date.
            cleaned["event_start"] = None
            cleaned["event_end"] = None
            cleaned["roles_needed"] = []
            if not cleaned.get("platforms"):
                self.add_error("platforms", "Pick at least one platform to post to.")

        return cleaned


class ProfilePhoneForm(forms.Form):
    """
    The one thing a team member may edit about themselves from their own
    profile — everything else there (points, strikes, skills, verticals,
    availability) is owned by the engine or an admin/domain-head elsewhere.
    """

    phone = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. 98765 43210"}),
    )


class VenueEditForm(forms.Form):
    """
    Change a Coverage request's venue after submission — venues routinely move
    at the last minute, and there was previously no way to correct one once a
    request had been raised (neither the old Express API nor this port ever
    had an edit endpoint at all).
    """

    venue = forms.CharField(
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"class": "input"}),
    )


class EventTimeForm(forms.Form):
    """
    Change a Coverage request's event *time*. Only two time-of-day fields, no
    dates: the view combines them with the event's existing dates, so a club can
    move "10:00–12:00" to "14:00–16:00" but has no way to move it to another day.
    """

    start_time = forms.TimeField(
        label="Start", widget=forms.TimeInput(attrs={"type": "time", "class": "input"}, format="%H:%M")
    )
    end_time = forms.TimeField(
        label="End", widget=forms.TimeInput(attrs={"type": "time", "class": "input"}, format="%H:%M")
    )


class SubEventForm(forms.ModelForm):
    """One sub-event: an item in the main event's schedule."""

    class Meta:
        model = SubEvent
        fields = ["name", "start", "end", "venue", "notes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "input", "placeholder": "e.g. Inauguration"}),
            "start": DateTimeLocalInput(attrs={"class": "input"}),
            "end": DateTimeLocalInput(attrs={"class": "input"}),
            "venue": forms.TextInput(attrs={"class": "input", "placeholder": "Venue (optional)"}),
            "notes": forms.TextInput(attrs={"class": "input", "placeholder": "Notes for the team (optional)"}),
        }

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        # Sub-events may sit outside the main event's window — only their own
        # start/end have to make sense.
        if start and end and end <= start:
            self.add_error("end", "A sub-event must end after it starts.")
        return cleaned


class MeetingForm(forms.Form):
    """
    Call (or edit) a team meeting. Who is invited is one of three modes — the
    whole team, chosen verticals, or chosen people — expressed with a radio and
    two multi-selects so it needs no JavaScript: the fields for the modes you
    didn't pick are simply ignored. `members` are the people who can be offered
    (active and not out of work, plus anyone already invited when editing).
    """

    title = forms.CharField(max_length=200, widget=forms.TextInput(attrs={"class": "input"}))
    start = forms.DateTimeField(widget=DateTimeLocalInput(attrs={"class": "input"}))
    end = forms.DateTimeField(widget=DateTimeLocalInput(attrs={"class": "input"}))
    venue = forms.CharField(
        max_length=300,
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Leave blank if the MOM person is booking it"}),
    )
    agenda = forms.CharField(required=False, widget=forms.Textarea(attrs={"class": "input", "rows": 4}))

    invite_mode = forms.ChoiceField(choices=(), widget=forms.RadioSelect, initial="all", label="Who is invited")
    verticals = forms.MultipleChoiceField(
        choices=[(v, v) for v in VERTICALS],
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Verticals",
        help_text="Anyone whose primary or secondary vertical is one of these.",
    )
    people = forms.MultipleChoiceField(
        choices=(), required=False, widget=forms.CheckboxSelectMultiple, label="People"
    )

    wants_mom = forms.BooleanField(
        required=False,
        label="Assign one person to take the minutes (MOM) and book the venue",
    )
    mom_email = forms.ChoiceField(
        required=False,
        label="Who",
        widget=forms.Select(attrs={"class": "input"}),
    )

    def __init__(self, *args, members=(), mom_candidates=(), require_future=True, **kwargs):
        from engine.meetings import INVITE_MODES  # local: keeps ui.forms importable without the engine

        super().__init__(*args, **kwargs)
        self.require_future = require_future
        self.fields["invite_mode"].choices = INVITE_MODES
        self.fields["people"].choices = [
            (m.email, f"{m.name}" + (f" · {m.vertical_label}" if m.vertical_label else "")) for m in members
        ]
        self.fields["mom_email"].choices = [("", "Pick automatically (fewest points)")] + [
            (m.email, m.name) for m in mom_candidates
        ]

    def clean(self):
        cleaned = super().clean()
        start, end = cleaned.get("start"), cleaned.get("end")
        if start and end and end <= start:
            self.add_error("end", "The meeting must end after it starts.")
        if start and self.require_future and start <= timezone.now():
            self.add_error("start", "That start time has already passed.")
        mode = cleaned.get("invite_mode")
        if mode == "verticals" and not cleaned.get("verticals"):
            self.add_error("verticals", "Pick at least one vertical.")
        if mode == "people" and not cleaned.get("people"):
            self.add_error("people", "Pick at least one person.")
        return cleaned


class MomForm(forms.Form):
    """Change who takes the minutes and books the venue, or clear it."""

    mom_email = forms.ChoiceField(required=False, label="Minutes + venue", widget=forms.Select(attrs={"class": "input"}))

    def __init__(self, *args, candidates=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["mom_email"].choices = [("", "Nobody")] + [(m.email, m.name) for m in candidates]


class RejectForm(forms.Form):
    reason = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3, "class": "input", "placeholder": "Reason (sent to the requester)"}),
        required=False,
        label="Reason",
        help_text="Sent to the requester. Worth filling in — a bare rejection invites a re-submission.",
    )


class ReassignForm(forms.Form):
    """
    Assign a task: leave the dropdown on its default to auto-pick the best
    available member, or choose someone by name. A single field rather than a
    mode toggle + a select, so the choice needs no JavaScript to express.
    """

    MODE_AUTO = "auto"
    MODE_MANUAL = "manual"

    member_email = forms.ChoiceField(
        label="Assign to", required=False, widget=forms.Select(attrs={"class": "input"})
    )

    def __init__(self, *args, eligible=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member_email"].choices = [("", "Auto-pick best available")] + [
            (m.email, f"{m.name}" + (f" · {m.vertical_label}" if m.vertical_label else ""))
            for m in eligible
        ]

    @property
    def mode(self) -> str:
        return self.MODE_MANUAL if self.cleaned_data.get("member_email") else self.MODE_AUTO


class AddTaskForm(forms.Form):
    """
    Bring one specific internal-only task type onto a request that didn't
    originally have it. `task_type` is fixed (rendered hidden) rather than a
    dropdown, because the member list on offer depends on which type it is —
    plain HTML can't react to a dropdown's own choice, so the view instead
    renders one small AddTaskForm per eligible task type, each already scoped
    to that type's real candidate list.
    """

    task_type = forms.CharField(widget=forms.HiddenInput)
    member_email = forms.ChoiceField(
        label="Assign to", required=False, widget=forms.Select(attrs={"class": "input"})
    )

    def __init__(self, *args, task_type: str, eligible=(), **kwargs):
        kwargs.setdefault("initial", {})["task_type"] = task_type
        super().__init__(*args, **kwargs)
        self.fields["member_email"].choices = [("", "Auto-pick best available")] + [
            (m.email, f"{m.name}" + (f" · {m.vertical_label}" if m.vertical_label else ""))
            for m in eligible
        ]

    @property
    def mode(self) -> str:
        return ReassignForm.MODE_MANUAL if self.cleaned_data.get("member_email") else ReassignForm.MODE_AUTO


class StrikeForm(forms.Form):
    """
    Manually give a strike (docs/PRD.md §5.7). Strikes are only ever given by
    hand — the deadline sweep no longer adds any. `member_email`'s choices are
    scoped by the view to whoever the caller is actually allowed to strike
    (`core.roles.can_strike`): secretary/admin see the whole team, a domain head
    sees only members of their own vertical (primary or secondary).

    Heads can give only a yellow strike, so `allow_red` is off for them and the
    colour dropdown simply doesn't offer red; the view re-checks with
    `can_strike` regardless, since a form field is not an authorisation.
    """

    member_email = forms.ChoiceField(label="Member", widget=forms.Select(attrs={"class": "input"}))
    color = forms.ChoiceField(
        label="Colour", choices=STRIKE_CHOICES, initial=STRIKE_YELLOW, widget=forms.Select(attrs={"class": "input"})
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Reason (recorded in the activity log)"}),
    )

    def __init__(self, *args, strikeable=(), allow_red=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member_email"].choices = [
            (m.email, f"{m.name}" + (f" · {m.vertical_label}" if m.vertical_label else "") + f" ({m.strike_label})")
            for m in strikeable
        ]
        if not allow_red:
            self.fields["color"].choices = [c for c in STRIKE_CHOICES if c[0] == STRIKE_YELLOW]


class RemoveStrikeForm(forms.Form):
    """
    Waive one strike of a chosen colour — secretary/admin only, unlike giving
    one (which a domain head may also do, yellow only, within their own
    vertical; see StrikeForm). The scope is narrower here on purpose: undoing a
    strike is a bigger call than giving one.
    """

    member_email = forms.ChoiceField(label="Member", widget=forms.Select(attrs={"class": "input"}))
    color = forms.ChoiceField(
        label="Colour", choices=STRIKE_CHOICES, initial=STRIKE_YELLOW, widget=forms.Select(attrs={"class": "input"})
    )
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Reason (recorded in the activity log)"}),
    )

    def __init__(self, *args, strikeable=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member_email"].choices = [
            (m.email, f"{m.name} ({m.strike_label})") for m in strikeable
        ]


class RemoveFromTeamForm(forms.Form):
    """
    Deactivate a team member (docs: "kick from the team"). Sets `active=False`
    rather than deleting the row — the assignment engine already excludes
    inactive members (`is_base_eligible`), the Dashboard already dims them,
    and their points/strikes/task history stay intact and it's reversible.
    Secretary/admin only.
    """

    member_email = forms.ChoiceField(label="Member", widget=forms.Select(attrs={"class": "input"}))
    reason = forms.CharField(
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "Reason (recorded in the activity log)"}),
    )

    def __init__(self, *args, removable=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["member_email"].choices = [
            (m.email, f"{m.name}" + (f" · {m.vertical_label}" if m.vertical_label else ""))
            for m in removable
        ]


class CommitteeForm(forms.ModelForm):
    """Add or update a requesting body. `last_seq` is engine-owned, so absent."""

    class Meta:
        model = Committee
        fields = ["email", "name", "acronym", "type", "campus"]
        labels = {"email": "Login email", "acronym": "Acronym (reference-code prefix)"}
        widgets = {
            "email": forms.EmailInput(attrs={"class": "input", "placeholder": "club@iimsirmaur.ac.in"}),
            "name": forms.TextInput(attrs={"class": "input"}),
            "acronym": forms.TextInput(attrs={"class": "input", "placeholder": "MKTG"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type"] = forms.ChoiceField(
            choices=[(t, t) for t in COMMITTEE_TYPES],
            initial="Committee",
            widget=forms.Select(attrs={"class": "input"}),
        )
        self.fields["campus"] = forms.ChoiceField(
            choices=[("", "—")] + [(c, c) for c in CAMPUSES],
            required=False,
            widget=forms.Select(attrs={"class": "input"}),
        )

    def clean_email(self):
        return (self.cleaned_data["email"] or "").strip().lower()

    def _get_validation_exclusions(self):
        # save() upserts on the email, so "this email already exists" must not be
        # a validation error — it would make the update path unreachable.
        exclude = super()._get_validation_exclusions()
        exclude.add("email")
        return exclude

    def save(self, commit=True):
        """
        Upsert on the login email rather than failing on the unique constraint —
        the Admin view's committee form is used to correct existing entries at
        least as often as to add new ones, and re-keying one by hand is a chore.
        `last_seq` is preserved so the reference counter never restarts.
        """
        email = self.cleaned_data["email"].strip().lower()
        existing = Committee.objects.filter(email=email).first()
        if existing is not None:
            for field in ("name", "acronym", "type", "campus"):
                setattr(existing, field, self.cleaned_data[field])
            if commit:
                existing.save()
            self.instance = existing
            return existing
        return super().save(commit=commit)


class VerticalHeadForm(forms.Form):
    member_email = forms.EmailField()
    vertical = forms.ChoiceField(
        choices=[("", "Remove as head")] + [(v, v) for v in VERTICALS], required=False
    )


class AvailabilityForm(forms.Form):
    member_email = forms.EmailField()
    availability = forms.ChoiceField(choices=[("available", "On work"), ("out", "Out of work")])


class MemberPhoneForm(forms.Form):
    """
    Secretary/admin editing a team member's contact number directly from the
    master roster (Admin view) — the same field a member can set themselves
    on their own profile (ui/views.py's `profile`). Whichever was set last
    wins; there's no separate "master" copy to reconcile.
    """

    member_email = forms.EmailField(widget=forms.HiddenInput)
    phone = forms.CharField(
        max_length=30,
        required=False,
        widget=forms.TextInput(attrs={"class": "input", "placeholder": "e.g. 98765 43210"}),
    )


class MemberVerticalsForm(forms.Form):
    """
    Secretary/admin editing a member's primary and secondary vertical from the
    master roster. A separate form and endpoint from the phone editor (rather
    than one shared row form) because a partial POST would otherwise blank
    whichever fields it didn't carry.

    Note: the seed command re-applies verticals from core/seed_data.py on every
    deploy, so a change that should outlive the next deploy belongs in that file
    too.
    """

    member_email = forms.EmailField(widget=forms.HiddenInput)
    vertical = forms.ChoiceField(choices=[("", "—")] + [(v, v) for v in VERTICALS], required=False)
    secondary_vertical = forms.ChoiceField(choices=[("", "—")] + [(v, v) for v in VERTICALS], required=False)

    def clean(self):
        cleaned = super().clean()
        primary, secondary = cleaned.get("vertical"), cleaned.get("secondary_vertical")
        if secondary and not primary:
            raise forms.ValidationError("Pick a primary vertical before a secondary one.")
        if primary and primary == secondary:
            raise forms.ValidationError("The secondary vertical must differ from the primary.")
        return cleaned


class PointSchemeForm(forms.ModelForm):
    """Admin-only editor for every constant in the scoring scheme."""

    class Meta:
        model = PointsScheme
        exclude: list[str] = []
        widgets = {
            field.name: forms.NumberInput(attrs={"class": "input", "min": "0"})
            for field in PointsScheme._meta.fields
            if field.name != "id"
        }


class TeamImportForm(forms.Form):
    """
    Bulk roster import (docs/PRD.md §5.10).

    Accepts a pasted CSV or an uploaded file. Re-importing updates existing
    members without resetting their points or strikes.

    Columns: name, email, vertical, secondaryVertical (optional), year, skills,
    campus, phone, active. A blank secondaryVertical *column* leaves an existing
    member's secondary vertical alone; only a file that has the column changes it.
    """

    csv_file = forms.FileField(required=False, label="CSV file")
    csv_text = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 8, "spellcheck": "false", "class": "input"}),
        label="…or paste CSV",
        help_text=(
            "Header row required: name,email,vertical,secondaryVertical,year,skills,campus,phone,active "
            "(secondaryVertical, domainHeadOf and active are optional)"
        ),
    )

    def clean(self):
        cleaned = super().clean()
        upload = cleaned.get("csv_file")
        text = (cleaned.get("csv_text") or "").strip()
        if not upload and not text:
            raise forms.ValidationError("Upload a CSV file or paste the rows.")
        if upload:
            try:
                cleaned["csv_text"] = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                raise forms.ValidationError(
                    "That file isn't UTF-8 text. Re-export it as CSV UTF-8 from your spreadsheet."
                )
        return cleaned
