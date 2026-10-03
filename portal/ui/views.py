"""
Portal views.

Server-rendered replacements for `web/js/views/*.js`. Each old SPA view fetched
JSON from an Express route and rendered it client-side; here the view queries
the database directly and renders a template — the round trip a browser used to
make to `api.js` is now just a Python function call, which is the whole reason
this re-platform can retire the SPA's JS entirely (see the plan's §6).

Authorization mirrors the old routes exactly: `core.roles.can_read_request` /
`can_assign` are the same functions the routes used, just called from a view
instead of a handler.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F, Q
from django.forms import inlineformset_factory
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from core.config import get_points_scheme
from core.constants import (
    CAMPUSES,
    COORDINATOR_GRACE_HOURS,
    DERIVED_EDITOR,
    EDIT_CUTOFF_HOURS,
    SUBEVENT_CUTOFF_HOURS,
    Availability,
    LeaveStatus,
    STRIKE_RED,
    STRIKE_YELLOW,
    TASK_EVENT_COORDINATOR,
    TASK_SUPERVISOR,
    RequestStatus,
    RequestType,
    TaskStatus,
)
from core.csv_import import import_rows, parse_csv
from core.decorators import secretary_or_admin_required, team_required
from core.models import (
    Committee,
    LeaveRequest,
    PointsScheme,
    Request,
    SubEvent,
    Task,
    TaskType,
    TeamMember,
)
from core.roles import (
    can_assign,
    can_change_event_time,
    can_edit_subevents,
    can_edit_venue,
    can_read_request,
    can_strike,
    subevent_is_open,
)
from engine.assign import choose_supervisor, eligible_members, pick_best
from engine.assignment import override_proposed_assignee, perform_swap, validate_member
from engine.confirm import confirm_request
from engine.event_changes import apply_event_time_change, notify_subevent_change
from engine.leave import open_leave_for, open_tasks_by_email, switch_availability
from engine.pipeline import compute_deadline, refresh_coordinator_deadline
from engine.points import base_points_for, scheme_examples
from engine.workflow import (
    complete_task,
    open_supervision_counts,
    process_new_request,
    reject_request,
    schedule_posts,
)
from services.calendar import calendar_service

from . import dashboard as dashboard_data
from .forms import (
    AddTaskForm,
    AvailabilityForm,
    CommitteeForm,
    EventTimeForm,
    LeaveRequestForm,
    MemberPhoneForm,
    MemberVerticalsForm,
    PointSchemeForm,
    ProfilePhoneForm,
    ReassignForm,
    RejectForm,
    RemoveFromTeamForm,
    RemoveStrikeForm,
    RequestForm,
    StrikeForm,
    SubEventForm,
    TeamImportForm,
    VenueEditForm,
    VerticalHeadForm,
)

DAY_SECONDS = 86400

#: Admin page tabs, in order. The first is the default.
ADMIN_TABS = [("team", "Team"), ("committees", "Committees"), ("setup", "Setup")]

#: Sub-event rows on the new-request form (multi-day events only). One blank row
#: is offered; the page's "+" button adds more. Untouched rows are skipped on
#: save, so an unused row costs nothing.
SubEventFormSet = inlineformset_factory(
    Request, SubEvent, form=SubEventForm, extra=1, can_delete=False
)


# ── Home / sign-in ───────────────────────────────────────────────────────────


def home(request):
    if request.user.is_authenticated:
        return redirect("request-list")
    return render(request, "ui/signin.html")


def signed_out(request):
    """Landing spot after a domain refusal or an explicit sign-out."""
    return render(request, "ui/signin.html")


# ── Profile ──────────────────────────────────────────────────────────────────


@login_required
def profile(request):
    roles = request.roles
    phone_form = None

    if roles.member is not None:
        if request.method == "POST":
            phone_form = ProfilePhoneForm(request.POST)
            if phone_form.is_valid():
                roles.member.phone = phone_form.cleaned_data["phone"].strip()
                roles.member.save(update_fields=["phone"])
                messages.success(request, "Phone number updated.")
                return redirect("profile")
        else:
            phone_form = ProfilePhoneForm(initial={"phone": roles.member.phone})

    context = {"roles": roles, "phone_form": phone_form}
    if roles.member is not None:
        member = roles.member
        context.update(
            {
                "is_out": member.availability == Availability.OUT,
                "open_leave": open_leave_for(member),
                "recent_leaves": member.leave_requests.exclude(status__in=LeaveStatus.OPEN)[:3],
                "leave_form": LeaveRequestForm(),
                "today": timezone.localdate(),
            }
        )
    return render(request, "ui/profile.html", context)


# ── Requests ─────────────────────────────────────────────────────────────────


@login_required
def request_new(request):
    roles = request.roles

    # The POC/Admin can enter a request *for* a club that hasn't (`?for=<committee id>`).
    # It then belongs to that club — its login is the contact, so it appears under the
    # club's My Requests and its ID uses the club's acronym — and the form behaves as
    # that club's: Coverage is offered and the requester is locked to the club's name.
    # Enforced here, not just hidden in the page.
    on_behalf = None
    for_id = request.GET.get("for", "")
    if for_id:
        if not roles.is_staff_side:
            raise PermissionDenied("Only the POC or an Admin can create a request for a club.")
        if not for_id.isdigit():
            raise Http404("No such committee.")
        on_behalf = get_object_or_404(Committee, pk=int(for_id))
    acting_committee = on_behalf or roles.committee
    is_committee = acting_committee is not None
    committee_name = acting_committee.name if is_committee else ""
    available_roles = list(
        TaskType.objects.filter(requestable=True).values_list("task", flat=True)
    )
    from core.models import Platform

    available_platforms = list(
        Platform.objects.filter(active=True).values_list("platform", flat=True).distinct()
    )

    if request.method == "POST":
        form = RequestForm(
            request.POST,
            is_committee=is_committee,
            available_roles=available_roles,
            available_platforms=available_platforms,
            committee_name=committee_name,
        )
        form_ok = form.is_valid()
        # Only a multi-day Coverage event has a schedule of sub-events when it is
        # raised: a single-day event is one venue and one time window (it can gain
        # sub-events later from its page). They're validated only then, and only
        # if the browser actually sent the rows — never for a Post or a single-day
        # event, whose stray rows are ignored rather than allowed to fail the
        # whole submission. A multi-day event's sub-events must sit inside its dates.
        is_coverage = form_ok and form.cleaned_data.get("type") == RequestType.COVERAGE
        multiday = is_coverage and form.is_multiday
        bounds = (form.cleaned_data["start_date"], form.cleaned_data["end_date"]) if multiday else None
        subevents = SubEventFormSet(
            request.POST, prefix="sub", instance=Request(), form_kwargs={"bounds": bounds}
        )
        wants_subevents = multiday and "sub-TOTAL_FORMS" in request.POST
        subevents_ok = subevents.is_valid() if wants_subevents else True

        if form_ok and subevents_ok:
            new_request = form.save(commit=False)
            # Content fields only — everything engine-owned is set here, never
            # taken from the form.
            new_request.contact_email = on_behalf.email if on_behalf else roles.email
            new_request.created_on_behalf_by = roles.email if on_behalf else ""
            new_request.status = RequestStatus.NEW
            new_request.is_multiday = multiday
            new_request.created_at = timezone.now()
            new_request.roles_needed = form.cleaned_data.get("roles_needed") or []
            new_request.platforms = form.cleaned_data.get("platforms") or []
            new_request.save()

            # Saved before the engine runs so the POC's approval email can list
            # the schedule.
            if wants_subevents:
                subevents.instance = new_request
                subevents.save()

            # A request entered for a club by the POC/Admin is accepted straight
            # away: they are the approver, and the club is emailed as usual.
            process_new_request(new_request, skip_approval=on_behalf is not None)
            new_request.refresh_from_db()

            # Post/Redirect/Get: back to a blank form (a refresh can't resubmit),
            # with the confirmation pop-up carried across in the session.
            request.session["request_submitted"] = {
                "ref": new_request.ref_code or "",
                "pending": new_request.status == RequestStatus.PENDING,
                "for": on_behalf.name if on_behalf else "",
            }
            return redirect(reverse("request-new") + (f"?for={on_behalf.pk}" if on_behalf else ""))
    else:
        initial = {"requester": committee_name if is_committee else (request.user.get_full_name() or roles.email)}
        form = RequestForm(
            is_committee=is_committee,
            available_roles=available_roles,
            available_platforms=available_platforms,
            committee_name=committee_name,
            initial=initial,
        )
        subevents = SubEventFormSet(prefix="sub", instance=Request())

    return render(
        request,
        "ui/request_new.html",
        {
            "form": form,
            "is_committee": is_committee,
            "acting_committee": acting_committee,
            "on_behalf": on_behalf,
            # Staff can pick a club to enter a request for.
            "committees": Committee.objects.order_by("name") if roles.is_staff_side else [],
            "subevents": subevents,
            # Shown once, on the blank form that follows a successful submit.
            "submitted": request.session.pop("request_submitted", None) if request.method == "GET" else None,
        },
    )


@login_required
def request_list(request):
    requests = Request.objects.filter(contact_email=request.roles.email)
    return render(request, "ui/request_list.html", {"requests": requests})


#: Lifecycle stages shown as a stepper on the request detail page
#: (short label, full status string) — mirrors STAGES in web/js/views/myRequests.js.
REQUEST_STAGES = [
    ("New", RequestStatus.NEW),
    ("Pending", RequestStatus.PENDING),
    ("Accepted", RequestStatus.ACCEPTED),
    ("Covered", RequestStatus.EVENT_COVERED),
    ("Ready", RequestStatus.READY_TO_POST),
    ("Posted", RequestStatus.POSTED),
]


def _stepper(status: str) -> list[dict]:
    if status == RequestStatus.REJECTED:
        return []
    index = next((i for i, (_, full) in enumerate(REQUEST_STAGES) if full == status), 0)
    return [
        {"label": label, "state": "done" if i < index else "current" if i == index else "todo"}
        for i, (label, _) in enumerate(REQUEST_STAGES)
    ]


@login_required
def request_detail(request, pk):
    request_obj = get_object_or_404(Request, pk=pk)
    if not can_read_request(request.roles, request_obj):
        raise PermissionDenied("You can't view this request.")
    tasks = request_obj.tasks.all()
    can_mark_ready = request_obj.status == RequestStatus.EVENT_COVERED and can_assign(
        request.roles, "", request_obj.coordinator_email
    )
    can_edit_venue_flag = (
        request_obj.type == RequestType.COVERAGE
        and not request_obj.is_multiday  # a multi-day event has no single venue
        and request_obj.status != RequestStatus.REJECTED
        and can_edit_venue(request.roles, request_obj)
    )

    # Schedule amendments — Coverage only. A single-day event's *time* can change
    # until 24 hours before it starts; a multi-day event has dates only, so no time
    # form. Sub-events each have their own cutoff: 48 hours before that sub-event.
    can_edit_schedule = can_change_event_time(request.roles, request_obj)
    can_amend_subevents = can_edit_subevents(request.roles, request_obj)
    time_form = None
    subevent_rows = []
    subevent_add_form = None
    if request_obj.type == RequestType.COVERAGE:
        bounds = _multiday_bounds(request_obj)
        if can_edit_schedule:
            time_form = EventTimeForm(
                initial={
                    "start_time": timezone.localtime(request_obj.event_start).time(),
                    "end_time": timezone.localtime(request_obj.event_end).time(),
                }
            )
        if can_amend_subevents:
            subevent_add_form = SubEventForm(prefix="new", bounds=bounds, enforce_lead=True)
        for sub in request_obj.sub_events.all():
            open_for_changes = can_amend_subevents and subevent_is_open(sub.start)
            subevent_rows.append(
                {
                    "sub": sub,
                    "form": SubEventForm(
                        instance=sub, prefix=f"se{sub.pk}", bounds=bounds, enforce_lead=True
                    )
                    if open_for_changes
                    else None,
                }
            )

    return render(
        request,
        "ui/request_detail.html",
        {
            "request_obj": request_obj,
            "tasks": tasks,
            "can_mark_ready": can_mark_ready,
            "can_edit_venue": can_edit_venue_flag,
            "venue_form": VenueEditForm(initial={"venue": request_obj.venue}),
            "stages": _stepper(request_obj.status),
            "can_edit_schedule": can_edit_schedule,
            "can_amend_subevents": can_amend_subevents,
            "time_form": time_form,
            "subevent_rows": subevent_rows,
            "subevent_add_form": subevent_add_form,
        },
    )


@login_required
@require_POST
def request_edit_venue(request, pk):
    """
    Correct a Coverage request's venue after submission (docs/PRD.md has no
    edit path today — this is a deliberate, narrowly-scoped addition, not a
    general request editor: only `venue` is mutable here, everything
    engine-owned stays untouched).

    Propagates onto every already-created task for the request (`Task.venue`
    is a denormalized copy, same as `event_name`/`event_start`/`event_end`),
    and emails everyone currently assigned so nobody shows up at the old room.
    """
    request_obj = get_object_or_404(Request, pk=pk)
    if request_obj.type != RequestType.COVERAGE:
        raise PermissionDenied("Only Coverage requests have a venue.")
    if request_obj.is_multiday:
        raise PermissionDenied("A multi-day event has no single venue — each sub-event has its own.")
    if request_obj.status == RequestStatus.REJECTED:
        raise PermissionDenied("This request has been rejected.")
    if not can_edit_venue(request.roles, request_obj):
        raise PermissionDenied("Not allowed to edit this request's venue.")

    form = VenueEditForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Couldn't update the venue — try again.")
        return redirect("request-detail", pk=request_obj.pk)

    old_venue = request_obj.venue or ""
    new_venue = form.cleaned_data["venue"].strip()
    if new_venue == old_venue:
        return redirect("request-detail", pk=request_obj.pk)

    with transaction.atomic():
        request_obj.venue = new_venue
        request_obj.save(update_fields=["venue"])
        # Tasks carry their own copy so My Tasks / the deadline sweep never
        # need to join back to the request just to show where to go.
        Task.objects.filter(request=request_obj).update(venue=new_venue)

    assignee_emails = sorted(
        {e for e in request_obj.tasks.values_list("email", flat=True) if e}
    )
    if assignee_emails:
        from services import email as email_service

        email_service.send(
            assignee_emails,
            f"[Venue changed] {request_obj.ref_code} — {request_obj.event_name}",
            (
                f"The venue for {request_obj.event_name} ({request_obj.ref_code}) has changed.\n\n"
                f"Was: {old_venue or '(not set)'}\n"
                f"Now: {new_venue or '(not set)'}\n"
            ),
        )

    from core.activity import log_activity

    log_activity(
        "venue-changed",
        request_obj=request_obj,
        ref_code=request_obj.ref_code,
        actor=request.roles.email,
        detail=f"Venue: {old_venue or '(none)'} -> {new_venue or '(none)'}",
    )
    messages.success(request, "Venue updated" + (f" — {len(assignee_emails)} assignee(s) notified." if assignee_emails else "."))
    return redirect("request-detail", pk=request_obj.pk)


@login_required
@require_POST
def request_edit_time(request, pk):
    """
    Move a Coverage request's start/end *times* after submission. The dates can
    never change: the form only carries times of day, and each is combined here
    with the date the event already has, so there is no input that could shift it.

    Allowed only for the requesting body (or POC/Admin) while the event's current
    start is more than 24 hours away (`can_change_event_time`). Everything that
    follows from a new time — task deadlines, calendar entries, who is told, and
    flagging anyone the new time clashes with — is `apply_event_time_change`'s job.
    """
    request_obj = get_object_or_404(Request, pk=pk)
    if request_obj.type != RequestType.COVERAGE:
        raise PermissionDenied("Only Coverage requests have an event time.")
    if request_obj.is_multiday:
        raise PermissionDenied("A multi-day event has dates only, no times to change.")
    if not can_change_event_time(request.roles, request_obj):
        raise PermissionDenied(
            "The time can only be changed by the requesting body, and only while the event is "
            "more than 24 hours away."
        )

    form = EventTimeForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid start and end time.")
        return redirect("request-detail", pk=request_obj.pk)

    old_start, old_end = request_obj.event_start, request_obj.event_end
    start_date = timezone.localtime(old_start).date()
    end_date = timezone.localtime(old_end).date()
    new_start = timezone.make_aware(datetime.combine(start_date, form.cleaned_data["start_time"]))
    new_end = timezone.make_aware(datetime.combine(end_date, form.cleaned_data["end_time"]))

    if new_end <= new_start:
        messages.error(request, "The event must end after it starts.")
        return redirect("request-detail", pk=request_obj.pk)
    if new_start <= timezone.now():
        messages.error(request, "That start time has already passed.")
        return redirect("request-detail", pk=request_obj.pk)
    if new_start <= timezone.now() + timedelta(hours=EDIT_CUTOFF_HOURS):
        # The cutoff is checked against the current start before we get here;
        # without this, moving the start earlier would pull the event inside the
        # window where the team is already committed.
        messages.error(
            request,
            f"The new start must be more than {EDIT_CUTOFF_HOURS} hours from now — "
            "contact the POC for a change that late.",
        )
        return redirect("request-detail", pk=request_obj.pk)
    if (new_start, new_end) == (old_start, old_end):
        return redirect("request-detail", pk=request_obj.pk)

    result = apply_event_time_change(request_obj, new_start, new_end, request.roles.email)
    note = "Time updated; the team has been told."
    if result["clashes"]:
        note += (
            f" {len(result['clashes'])} assigned member(s) have a calendar clash — "
            "the POC and Task Supervisor have been alerted."
        )
    messages.success(request, note)
    return redirect("request-detail", pk=request_obj.pk)


def _subevent_request(request, pk) -> Request:
    """Load the request a sub-event view acts on, or raise if the caller can't amend its schedule."""
    request_obj = get_object_or_404(Request, pk=pk)
    if not can_edit_subevents(request.roles, request_obj):
        raise PermissionDenied("Sub-events can only be changed by the requesting body or the POC/Admin.")
    return request_obj


def _multiday_bounds(request_obj):
    """(first date, last date) of a multi-day event, else None — its sub-events must fall inside."""
    if not request_obj.is_multiday or not request_obj.event_start or not request_obj.event_end:
        return None
    return (
        timezone.localtime(request_obj.event_start).date(),
        timezone.localtime(request_obj.event_end).date(),
    )


def _too_late_for_subevent(request, request_obj) -> "HttpResponseRedirect":
    messages.error(
        request,
        f"A sub-event can only be added, changed or removed until {SUBEVENT_CUTOFF_HOURS} hours "
        "before it starts. Contact the POC for anything later.",
    )
    return redirect("request-detail", pk=request_obj.pk)


@login_required
@require_POST
def request_subevent_add(request, pk):
    request_obj = _subevent_request(request, pk)
    form = SubEventForm(
        request.POST, prefix="new", bounds=_multiday_bounds(request_obj), enforce_lead=True
    )
    if not form.is_valid():
        messages.error(request, "Couldn't add that sub-event: " + _first_error(form))
        return redirect("request-detail", pk=request_obj.pk)
    sub = form.save(commit=False)
    sub.request = request_obj
    sub.save()
    notify_subevent_change(request_obj, "added", sub, request.roles.email)
    messages.success(request, f"Sub-event “{sub.name}” added.")
    return redirect("request-detail", pk=request_obj.pk)


@login_required
@require_POST
def request_subevent_edit(request, pk, sub_pk):
    request_obj = _subevent_request(request, pk)
    sub = get_object_or_404(SubEvent, pk=sub_pk, request=request_obj)
    # Judged on the start it has *now* (before the form overwrites it), and the new
    # start is checked by the form too, so a sub-event can't be dragged into the
    # last 48 hours to dodge the cutoff.
    if not subevent_is_open(sub.start):
        return _too_late_for_subevent(request, request_obj)
    form = SubEventForm(
        request.POST, instance=sub, prefix=f"se{sub.pk}", bounds=_multiday_bounds(request_obj), enforce_lead=True
    )
    if not form.is_valid():
        messages.error(request, "Couldn't update that sub-event: " + _first_error(form))
        return redirect("request-detail", pk=request_obj.pk)
    if form.has_changed():
        sub = form.save()
        notify_subevent_change(request_obj, "edited", sub, request.roles.email)
        messages.success(request, f"Sub-event “{sub.name}” updated.")
    return redirect("request-detail", pk=request_obj.pk)


@login_required
@require_POST
def request_subevent_delete(request, pk, sub_pk):
    request_obj = _subevent_request(request, pk)
    sub = get_object_or_404(SubEvent, pk=sub_pk, request=request_obj)
    if not subevent_is_open(sub.start):
        return _too_late_for_subevent(request, request_obj)
    # Keep a copy for the notification: once deleted, the row's details are gone.
    snapshot = SubEvent(name=sub.name, start=sub.start, end=sub.end, venue=sub.venue, notes=sub.notes)
    sub.delete()
    notify_subevent_change(request_obj, "deleted", snapshot, request.roles.email)
    messages.success(request, f"Sub-event “{snapshot.name}” removed.")
    return redirect("request-detail", pk=request_obj.pk)


def _first_error(form) -> str:
    """One readable line out of a form's errors, for a flash message."""
    for errors in form.errors.values():
        return errors[0]
    return "check the values and try again."


# ── Tasks ────────────────────────────────────────────────────────────────────


@team_required
def task_list(request):
    tasks = Task.objects.filter(email=request.roles.email).select_related("request").order_by(
        "deadline"
    )
    return render(request, "ui/task_list.html", {"tasks": tasks})


@team_required
@require_POST
def task_complete(request, pk):
    task = get_object_or_404(Task, pk=pk)
    if (task.email or "").lower() != request.roles.email:
        raise PermissionDenied("Not your task.")
    if task.task == TASK_SUPERVISOR:
        # No "Mark done" exists for it — it closes itself in engine.workflow when
        # the Event Coordinator finishes. Refused server-side too, not just hidden.
        messages.error(
            request, "Supervising closes on its own when the Event Coordinator marks the event done."
        )
        return redirect("task-list")
    if not task.is_completable:
        messages.error(request, "That task isn't open for completion.")
        return redirect("task-list")
    if task.awaits_event:
        messages.error(request, "You can't mark this done until the event has started.")
        return redirect("task-list")

    # The Event Coordinator's own completion is how coverage gets handed back
    # to the requesting club -- they attach the drive link right here, and
    # engine.workflow.complete_task emails the club once it's saved.
    is_coverage_handoff = task.task == TASK_EVENT_COORDINATOR and task.req_type == RequestType.COVERAGE
    if is_coverage_handoff:
        drive_link = request.POST.get("content_links", "").strip()
        if not drive_link:
            messages.error(request, "Add the drive link before marking coverage complete.")
            return redirect("task-list")
        task.request.content_links = drive_link
        task.request.save(update_fields=["content_links"])

    # Claim the transition atomically: a double-clicked "Mark done" sends two
    # requests that both pass the is_completable check above, and each would
    # otherwise credit the points and email the club.
    claimed = Task.objects.filter(pk=task.pk, status__in=TaskStatus.COMPLETABLE).update(
        status=TaskStatus.DONE, completed_at=timezone.now()
    )
    if not claimed:
        messages.error(request, "That task isn't open for completion.")
        return redirect("task-list")
    task.refresh_from_db()
    complete_task(task)
    messages.success(request, f"{task.task} marked done.")
    return redirect("task-list")


# ── Assignments ──────────────────────────────────────────────────────────────


@login_required
def assignment_list(request):
    roles = request.roles
    if not roles.can_reach_assignments:
        raise PermissionDenied("You don't have an assignment role.")

    # Staff see every request. Everyone else sees the ones they coordinate,
    # supervise, or (as a head) have a task on in their vertical. Being a
    # second-year on its own no longer shows you anything.
    if roles.is_staff_side:
        task_qs = Task.objects.all()
    else:
        scope = Q(coordinator_email=roles.email) | Q(request__supervisor_email=roles.email)
        if roles.domain_head_of:
            scope |= Q(vertical=roles.domain_head_of)
        task_qs = Task.objects.filter(scope).distinct()

    requests = (
        Request.objects.filter(pk__in=task_qs.values_list("request_id", flat=True))
        .exclude(status__in=RequestStatus.TERMINAL)
        .prefetch_related("tasks")
    )

    # Secretary/admin/domain-head all land here already, which is why the
    # manual-strike form lives on this page rather than /portal-admin/ (which
    # a domain head can't reach at all). Heads can only ever give yellow, so the
    # colour choice offers red to staff alone.
    strikeable = [m for m in TeamMember.objects.all() if can_strike(roles, m, STRIKE_YELLOW)]
    strike_form = (
        StrikeForm(strikeable=strikeable, allow_red=roles.is_staff_side) if strikeable else None
    )

    return render(
        request,
        "ui/assignment_list.html",
        {"requests": requests, "strike_form": strike_form},
    )


@login_required
@require_POST
def issue_strike(request):
    """
    Manually give one strike, yellow or red, to a team member (docs/PRD.md §5.7)
    — a deliberate human judgment call; nothing in the engine issues strikes.
    Scoped by `can_strike`: secretary/admin may give either colour to anyone, a
    domain head only a yellow, and only to a member of their own vertical
    (primary or secondary).
    """
    roles = request.roles
    strikeable = [m for m in TeamMember.objects.all() if can_strike(roles, m, STRIKE_YELLOW)]
    form = StrikeForm(request.POST, strikeable=strikeable, allow_red=roles.is_staff_side)
    if not form.is_valid():
        messages.error(request, "Pick a member and a colour you're allowed to give.")
        return redirect("assignment-list")

    member = get_object_or_404(TeamMember, email=form.cleaned_data["member_email"])
    color = form.cleaned_data["color"]
    # The form's choices are a convenience, not the authority: check again.
    if not can_strike(roles, member, color):
        raise PermissionDenied("Not allowed to give this member that strike.")

    reason = form.cleaned_data.get("reason", "").strip()
    column = "red_strikes" if color == STRIKE_RED else "yellow_strikes"
    TeamMember.objects.filter(pk=member.pk).update(**{column: F(column) + 1})

    from core.activity import log_activity

    log_activity(
        "strike",
        actor=roles.email,
        member=member.email,
        detail=f"{color.title()} strike by {roles.email}" + (f": {reason}" if reason else ""),
    )
    messages.success(request, f"{color.title()} strike given to {member.name}.")
    return redirect("assignment-list")


def _in_assignment_scope(roles, request_obj, tasks) -> bool:
    """
    Mirrors assignment_list's own filtering: staff see every request, everyone
    else only the ones they coordinate, supervise, or (as a head) have a task on
    in their vertical. Shared by the detail, add and reassign views so a head
    can't add a task to (and thereby unlock the page of) a request outside it.
    """
    if roles.is_staff_side:
        return True
    is_coordinator = roles.email == (request_obj.coordinator_email or "").lower()
    is_supervisor = roles.email == (request_obj.supervisor_email or "").lower()
    has_domain_task = bool(roles.domain_head_of) and any(
        t.vertical == roles.domain_head_of for t in tasks
    )
    return is_coordinator or is_supervisor or has_domain_task


@login_required
def assignment_detail(request, pk):
    roles = request.roles
    if not roles.can_reach_assignments:
        raise PermissionDenied("You don't have an assignment role.")

    request_obj = get_object_or_404(Request, pk=pk)
    tasks = list(request_obj.tasks.all())

    if not _in_assignment_scope(roles, request_obj, tasks):
        raise PermissionDenied("You don't have an assignment role on this request.")

    settings = _settings()
    team = _team()

    task_rows = []
    for task in tasks:
        manageable = can_assign(roles, task.vertical, request_obj.coordinator_email, task.task)
        form = None
        if manageable:
            # The dropdown deliberately spans the whole active pool, not just
            # this task's vertical/skill — a coordinator may need to pull
            # someone in from elsewhere as a stopgap. require_skill=False only
            # relaxes that one check; active/campus/calendar and the year rule
            # (second-years only supervise) still apply. Auto-pick (leaving the
            # dropdown on its default) stays skill-matched — see
            # assignment_reassign.
            eligible = eligible_members(
                task.required_skill,
                task.at_event,
                request_obj,
                settings,
                team,
                calendar_service(),
                exclude={task.email} if task.email else set(),
                require_skill=False,
                task_name=task.task,
                vertical=task.vertical,
            )
            form = ReassignForm(eligible=eligible)
        task_rows.append({"task": task, "manageable": manageable, "form": form})

    # One small add-task form per internal-assignable type not already fully
    # staffed — each already scoped to that type's own candidate pool, so no
    # task-type-dependent dropdown (and no JS) is needed (ui/forms.py).
    existing_types = {t.task for t in tasks}
    add_forms = []
    for task_type in TaskType.objects.filter(internal_assignable=True):
        if task_type.task != "Event Coordinator" and task_type.task in existing_types:
            continue  # already on this request; reassign it instead of adding again
        if task_type.task == TASK_SUPERVISOR and request_obj.type != RequestType.COVERAGE:
            continue  # only Coverage requests have a supervisor
        if not can_assign(roles, task_type.vertical, request_obj.coordinator_email, task_type.task):
            continue
        eligible = eligible_members(
            task_type.required_skill,
            task_type.at_event,
            request_obj,
            settings,
            team,
            calendar_service(),
            task_name=task_type.task,
            vertical=task_type.vertical,
        )
        add_forms.append(
            {"task_type": task_type, "form": AddTaskForm(task_type=task_type.task, eligible=eligible)}
        )

    return render(
        request,
        "ui/assignment_detail.html",
        {
            "request_obj": request_obj,
            "task_rows": task_rows,
            "add_forms": add_forms,
            "can_manage_any": any(row["manageable"] for row in task_rows),
            "can_mark_ready": can_assign(roles, "", request_obj.coordinator_email),
        },
    )


def _settings():
    from core.config import get_settings

    return get_settings()


def _team():
    from core.config import get_team

    return get_team()


@login_required
@require_POST
def assignment_reassign(request, pk):
    roles = request.roles
    task = get_object_or_404(Task, pk=pk)
    request_obj = task.request

    # The Task Supervisor is staff-only — `can_assign` says so by task name, so
    # the event coordinator (whose work it oversees) and heads are refused here
    # even though they may reassign everything else on the request.
    if not can_assign(roles, task.vertical, request_obj.coordinator_email, task.task):
        raise PermissionDenied("Not allowed to reassign this task.")
    if request_obj.status in RequestStatus.TERMINAL:
        messages.error(request, "That request is closed — its team can no longer be changed.")
        return redirect("assignment-detail", pk=request_obj.pk)

    settings = _settings()
    exclude = {task.email} if task.email else set()

    # The manual-pick pool spans the whole eligible group (any vertical) — the
    # form must be bound against the same broad list it was rendered with, or a
    # cross-vertical pick fails validation as "not a valid choice".
    any_vertical = eligible_members(
        task.required_skill, task.at_event, request_obj, settings, _team(),
        calendar_service(), exclude, require_skill=False,
        task_name=task.task, vertical=task.vertical,
    )
    form = ReassignForm(request.POST, eligible=any_vertical)
    if not form.is_valid():
        messages.error(request, "That selection isn't valid — try again.")
        return redirect("assignment-detail", pk=request_obj.pk)

    if form.mode == ReassignForm.MODE_MANUAL:
        # A deliberate hand-pick may cross verticals; every other eligibility
        # rule (active, campus, calendar, second-years-only-supervise) still applies.
        validation = validate_member(
            form.cleaned_data["member_email"],
            task.required_skill,
            task.at_event,
            request_obj,
            settings,
            require_skill=False,
            task_name=task.task,
        )
        if not validation.ok:
            messages.error(request, validation.reason)
            return redirect("assignment-detail", pk=request_obj.pk)
        member = validation.member
    elif task.task == TASK_SUPERVISOR:
        # Auto-pick a supervisor the same way the engine does: the second-year
        # holding the fewest open supervisions, other than the current one.
        candidates = [m for m in _team() if (m.email or "").lower() not in {e.lower() for e in exclude}]
        choice = choose_supervisor(request_obj, settings, candidates, open_supervision_counts())
        if choice.member is None:
            messages.error(request, f"No eligible Task Supervisor: {choice.reason}.")
            return redirect("assignment-detail", pk=request_obj.pk)
        member = choice.member
    else:
        # Left on "auto-pick": stay skill-matched, so the engine doesn't
        # silently hand a photography task to a videographer just because
        # nobody chose anyone.
        skill_matched = eligible_members(
            task.required_skill, task.at_event, request_obj, settings, _team(),
            calendar_service(), exclude, task_name=task.task, vertical=task.vertical,
        )
        if not skill_matched:
            messages.error(
                request,
                f'No eligible member with "{task.required_skill}".'
                if task.required_skill
                else "No eligible member.",
            )
            return redirect("assignment-detail", pk=request_obj.pk)
        member = pick_best(skill_matched, vertical=task.vertical)

    perform_swap(task, member, request_obj)
    messages.success(request, f"{task.task} reassigned to {member.name}.")
    return redirect("assignment-detail", pk=request_obj.pk)


@login_required
@require_POST
def assignment_add(request, request_pk):
    roles = request.roles
    request_obj = get_object_or_404(Request, pk=request_pk)

    task_type_name = request.POST.get("task_type", "")
    task_type = get_object_or_404(TaskType, task=task_type_name, internal_assignable=True)
    if not can_assign(roles, task_type.vertical, request_obj.coordinator_email, task_type.task):
        raise PermissionDenied("Not allowed to add this task.")
    if not _in_assignment_scope(roles, request_obj, list(request_obj.tasks.all())):
        raise PermissionDenied("You don't have an assignment role on this request.")
    if request_obj.status in RequestStatus.TERMINAL:
        messages.error(request, "That request is closed — its team can no longer be changed.")
        return redirect("assignment-detail", pk=request_obj.pk)
    if task_type.task == TASK_SUPERVISOR and request_obj.type != RequestType.COVERAGE:
        raise PermissionDenied("Only Coverage requests have a Task Supervisor.")
    if task_type.task == TASK_SUPERVISOR and request_obj.tasks.filter(task=TASK_SUPERVISOR).exists():
        messages.error(request, "This request already has a Task Supervisor — reassign it instead.")
        return redirect("assignment-detail", pk=request_obj.pk)

    settings = _settings()

    # Event Coordinator is unique per request — adding it again reassigns the
    # existing one instead of creating a duplicate.
    existing_coordinator = (
        request_obj.tasks.filter(task="Event Coordinator").first()
        if task_type.task == "Event Coordinator"
        else None
    )
    exclude = {existing_coordinator.email} if existing_coordinator and existing_coordinator.email else set()

    eligible = eligible_members(
        task_type.required_skill, task_type.at_event, request_obj, settings, _team(), calendar_service(), exclude,
        task_name=task_type.task, vertical=task_type.vertical,
    )
    form = AddTaskForm(request.POST, task_type=task_type.task, eligible=eligible)
    if not form.is_valid():
        messages.error(request, "That selection isn't valid — try again.")
        return redirect("assignment-detail", pk=request_obj.pk)

    member = _resolve_member(
        form, eligible, task_type.required_skill, task_type.at_event, request_obj, settings,
        task_name=task_type.task, vertical=task_type.vertical,
    )
    if member is None:
        messages.error(
            request,
            f'No eligible member with "{task_type.required_skill}".'
            if task_type.required_skill
            else "No eligible member.",
        )
        return redirect("assignment-detail", pk=request_obj.pk)

    if existing_coordinator is not None:
        perform_swap(existing_coordinator, member, request_obj)
        messages.success(request, f"Event Coordinator reassigned to {member.name}.")
        return redirect("assignment-detail", pk=request_obj.pk)

    confirmed_state = request_obj.status in RequestStatus.CONFIRMED_STATES
    is_new_coordinator = task_type.task == TASK_EVENT_COORDINATOR
    with transaction.atomic():
        # points_awarded stays False -- points are earned on completion (see
        # engine/workflow.py's `_award_completion_points`), not on assignment.
        new_task = Task.objects.create(
            request=request_obj,
            req_type=request_obj.type,
            ref_code=request_obj.ref_code or "",
            task=task_type.task,
            required_skill=task_type.required_skill,
            at_event=task_type.at_event,
            vertical=task_type.vertical or "",
            member=member.name,
            email=member.email,
            phone=member.phone or "",
            # The same scheme automatic assignment uses (coordinator 20, supervisor 0,
            # everything else the domain-task base), not the per-type legacy number.
            points=base_points_for(task_type.task, get_points_scheme()),
            deadline=compute_deadline(task_type, request_obj, timezone.now()),
            status=TaskStatus.CONFIRMED if confirmed_state else TaskStatus.PROPOSED,
            coordinator_email=member.email if is_new_coordinator else (request_obj.coordinator_email or ""),
            event_name=request_obj.event_name or "",
            event_start=request_obj.event_start,
            event_end=request_obj.event_end,
            venue=request_obj.venue or "",
        )
        if is_new_coordinator:
            # A request that had no coordinator (a Post) must now point at this
            # one, or they can't open it and the other tasks escalate to nobody.
            request_obj.coordinator_email = member.email
            request_obj.save(update_fields=["coordinator_email"])
            Task.objects.filter(request=request_obj).exclude(pk=new_task.pk).update(
                coordinator_email=member.email
            )
        if task_type.task == TASK_SUPERVISOR:
            request_obj.supervisor_email = member.email
            request_obj.save(update_fields=["supervisor_email"])

        # Each shooter edits their own work: an extra photographer/videographer
        # arrives with their own editing task, whether or not they hold the
        # editing skill.
        new_editor = None
        editor_type = TaskType.objects.filter(task=DERIVED_EDITOR.get(task_type.task, "")).first()
        if editor_type is not None:
            new_editor = Task.objects.create(
                request=request_obj,
                req_type=request_obj.type,
                ref_code=request_obj.ref_code or "",
                task=editor_type.task,
                required_skill=editor_type.required_skill,
                at_event=editor_type.at_event,
                vertical=editor_type.vertical or "",
                member=member.name,
                email=member.email,
                phone=member.phone or "",
                points=base_points_for(editor_type.task, get_points_scheme()),
                deadline=compute_deadline(editor_type, request_obj, timezone.now()),
                status=new_task.status,
                coordinator_email=request_obj.coordinator_email or "",
                event_name=request_obj.event_name or "",
                event_start=request_obj.event_start,
                event_end=request_obj.event_end,
                venue=request_obj.venue or "",
                paired_task=new_task,
            )

        # The coordinator is due after the last of the others, and one just arrived.
        refresh_coordinator_deadline(request_obj)

    from engine.notify import notify_assignee
    from core.activity import log_activity

    notify_assignee(new_task)
    if new_editor is not None:
        notify_assignee(new_editor)
    log_activity(
        "manual-assign",
        request_obj=request_obj,
        ref_code=request_obj.ref_code,
        member=member.email,
        detail=f"{task_type.task} -> {member.name} ({form.mode})"
        + (f", with their {new_editor.task}" if new_editor is not None else ""),
    )
    messages.success(
        request,
        f"{task_type.task} added, assigned to {member.name}"
        + (f", along with their {new_editor.task}." if new_editor is not None else "."),
    )
    return redirect("assignment-detail", pk=request_obj.pk)


def _resolve_member(form, eligible, required_skill, at_event, request_obj, settings, *, task_name="", vertical=""):
    """
    Validate a manual pick against the real rules, or auto-pick from `eligible`: the
    best on vertical and points, and among those still tied, one at random.
    """
    if form.mode == ReassignForm.MODE_MANUAL:
        validation = validate_member(
            form.cleaned_data["member_email"], required_skill, at_event, request_obj, settings,
            task_name=task_name,
        )
        return validation.member if validation.ok else None
    return pick_best(eligible, vertical=vertical)


@login_required
@require_POST
def mark_ready_to_post(request, request_pk):
    roles = request.roles
    request_obj = get_object_or_404(Request, pk=request_pk)

    if not can_assign(roles, "", request_obj.coordinator_email):
        raise PermissionDenied("Not allowed to mark this request ready.")
    if request_obj.status != RequestStatus.EVENT_COVERED:
        messages.error(request, "Request is not Event Covered yet.")
        return redirect("request-detail", pk=request_obj.pk)

    # Claim the transition atomically so a double-click can't run schedule_posts
    # twice (duplicate Post tasks, handler credited twice).
    claimed = Request.objects.filter(pk=request_obj.pk, status=RequestStatus.EVENT_COVERED).update(
        status=RequestStatus.READY_TO_POST, ready_by=roles.email, ready_at=timezone.now()
    )
    if not claimed:
        messages.error(request, "Request is not Event Covered yet.")
        return redirect("request-detail", pk=request_obj.pk)
    request_obj.refresh_from_db()
    try:
        schedule_posts(request_obj)
    except Exception:
        # Don't strand it at "Ready To post" with no posts and no way to retry.
        Request.objects.filter(pk=request_obj.pk, status=RequestStatus.READY_TO_POST).update(
            status=RequestStatus.EVENT_COVERED
        )
        raise
    messages.success(request, "Marked ready to post — scheduling in progress.")
    return redirect("request-detail", pk=request_obj.pk)


# ── Approvals ────────────────────────────────────────────────────────────────


@secretary_or_admin_required
def approval_list(request):
    pending = Request.objects.filter(status=RequestStatus.PENDING)
    # Out-of-work requests are decided here too. Each comes with the member's open
    # tasks, because approving never moves them: the POC reassigns by hand.
    pending_leaves = list(
        LeaveRequest.objects.filter(status=LeaveStatus.PENDING)
        .select_related("member")
        .order_by("start_date", "requested_at")
    )
    tasks_by_email = open_tasks_by_email([leave.member for leave in pending_leaves])
    leaves = [{"leave": leave, "tasks": tasks_by_email[leave.member.email]} for leave in pending_leaves]
    return render(request, "ui/approval_list.html", {"requests": pending, "leaves": leaves})


@secretary_or_admin_required
def approval_detail(request, pk):
    request_obj = get_object_or_404(Request, pk=pk)
    tasks = request_obj.tasks.all()

    # A Coverage request's Task Supervisor is only a suggestion at this point
    # (engine/workflow.py auto-picked the least-busy second-year) — offer the
    # approver the chance to choose someone else right here, same pattern as
    # manual reassignment elsewhere (blank = keep the suggestion). Post requests
    # have no supervisor.
    sup_task = None
    sup_form = None
    if request_obj.type == RequestType.COVERAGE:
        sup_task = tasks.filter(task=TASK_SUPERVISOR).first()
        if sup_task is not None:
            eligible = eligible_members(
                sup_task.required_skill,
                sup_task.at_event,
                request_obj,
                _settings(),
                _team(),
                calendar_service(),
                require_skill=False,
                task_name=TASK_SUPERVISOR,
            )
            sup_form = ReassignForm(eligible=eligible)

    return render(
        request,
        "ui/approval_detail.html",
        {
            "request_obj": request_obj,
            "tasks": tasks,
            "reject_form": RejectForm(),
            "sup_task": sup_task,
            "sup_form": sup_form,
        },
    )


@secretary_or_admin_required
@require_POST
def approval_decide(request, pk):
    request_obj = get_object_or_404(Request, pk=pk)
    if request_obj.status != RequestStatus.PENDING:
        messages.error(request, "That request is no longer pending approval.")
        return redirect("approval-list")

    decision = request.POST.get("decision")
    if decision == "approve":
        if request_obj.type == RequestType.COVERAGE:
            sup_task = request_obj.tasks.filter(task=TASK_SUPERVISOR).first()
            override_email = request.POST.get("member_email", "").strip().lower()
            if sup_task is not None and override_email and override_email != (sup_task.email or "").lower():
                validation = validate_member(
                    override_email,
                    sup_task.required_skill,
                    sup_task.at_event,
                    request_obj,
                    _settings(),
                    require_skill=False,
                    task_name=TASK_SUPERVISOR,
                )
                if not validation.ok:
                    messages.error(request, f"Task Supervisor: {validation.reason}")
                    return redirect("approval-detail", pk=request_obj.pk)
                override_proposed_assignee(sup_task, validation.member)
        confirm_request(request_obj)
        request_obj.refresh_from_db(fields=["status"])
        if request_obj.status == RequestStatus.REJECTED:
            messages.error(request, "That request was already rejected by someone else.")
        else:
            messages.success(request, f"{request_obj.ref_code} approved.")
    elif decision == "reject":
        form = RejectForm(request.POST)
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        if reject_request(request_obj, reason, request.roles.email):
            messages.success(request, f"{request_obj.ref_code} rejected.")
        else:
            messages.error(request, "That request was already decided by someone else.")
    else:
        messages.error(request, "Unrecognised decision.")
    return redirect("approval-list")


# ── Dashboard ────────────────────────────────────────────────────────────────


@secretary_or_admin_required
def dashboard(request):
    filters = {
        "period": request.GET.get("period", "all"),
        "campus": request.GET.get("campus", "all"),
        "year": request.GET.get("year", "all"),
        "vertical": request.GET.get("vertical", "all"),
    }
    stats = dashboard_data.compute_stats(filters)
    return render(request, "ui/dashboard.html", {"stats": stats, "filters": filters})


# ── Admin ────────────────────────────────────────────────────────────────────


@secretary_or_admin_required
def portal_admin(request):
    from core.constants import VERTICALS

    # One group at a time. The tab is remembered for the session so the many
    # actions that redirect back here land on the tab you were working in.
    tab = request.GET.get("tab") or request.session.get("admin_tab") or ADMIN_TABS[0][0]
    if tab not in dict(ADMIN_TABS):
        tab = ADMIN_TABS[0][0]
    request.session["admin_tab"] = tab

    context = {
        "tab": tab,
        "tabs": ADMIN_TABS,
        "is_admin": request.roles.is_admin,
        "verticals": VERTICALS,
        "coordinator_grace_hours": COORDINATOR_GRACE_HOURS,
    }

    if tab == "team":
        everyone = list(TeamMember.objects.all())
        strikeable = [m for m in everyone if m.yellow_strikes > 0 or m.red_strikes > 0]
        removable = [m for m in everyone if m.active]

        # The roster can be narrowed by campus / year / vertical, exactly like the
        # Dashboard (same rule, `member_matches_filters`). Only the table is
        # filtered: the strike and removal pickers below still list everyone.
        filters = {
            "campus": request.GET.get("campus", "all"),
            "year": request.GET.get("year", "all"),
            "vertical": request.GET.get("vertical", "all"),
        }
        members = [m for m in everyone if dashboard_data.member_matches_filters(m, **filters)]
        context.update(
            {
                "members": members,
                "total_members": len(everyone),
                "filters": filters,
                "filters_active": any(v != "all" for v in filters.values()),
                "campuses": CAMPUSES,
                # Passed back by the row forms so saving a row returns to this same view.
                "here": request.get_full_path(),
                "remove_strike_form": RemoveStrikeForm(strikeable=strikeable) if strikeable else None,
                "remove_from_team_form": RemoveFromTeamForm(removable=removable) if removable else None,
            }
        )
    elif tab == "committees":
        context.update({"committees": Committee.objects.all(), "committee_form": CommitteeForm()})
    else:  # setup: vertical heads, CSV import, point scheme
        members = list(TeamMember.objects.all())
        scheme = PointsScheme.load()
        context.update(
            {
                "members": members,
                "heads": {m.domain_head_of: m for m in members if m.domain_head_of},
                "team_import_form": TeamImportForm(),
                "point_form": PointSchemeForm(instance=scheme) if request.roles.is_admin else None,
                "examples": scheme_examples(scheme) if request.roles.is_admin else None,
            }
        )
    return render(request, "ui/admin.html", context)


@secretary_or_admin_required
@require_POST
def remove_strike(request):
    """Waive one strike of a chosen colour. Secretary/admin only — see RemoveStrikeForm."""
    strikeable = [m for m in TeamMember.objects.all() if m.yellow_strikes > 0 or m.red_strikes > 0]
    form = RemoveStrikeForm(request.POST, strikeable=strikeable)
    if not form.is_valid():
        messages.error(request, "Pick a member with a strike to remove.")
        return redirect("portal-admin")

    member = get_object_or_404(TeamMember, email=form.cleaned_data["member_email"])
    color = form.cleaned_data["color"]
    column = "red_strikes" if color == STRIKE_RED else "yellow_strikes"
    if getattr(member, column) <= 0:
        messages.error(request, f"{member.name} has no {color} strikes to remove.")
        return redirect("portal-admin")

    reason = form.cleaned_data.get("reason", "").strip()
    # The >0 check is repeated in the UPDATE itself: two submissions at once both
    # pass the read above and would otherwise take the count to -1.
    removed = TeamMember.objects.filter(pk=member.pk, **{f"{column}__gt": 0}).update(
        **{column: F(column) - 1}
    )
    if not removed:
        messages.error(request, f"{member.name} has no {color} strikes to remove.")
        return redirect("portal-admin")

    from core.activity import log_activity

    log_activity(
        "strike-removed",
        actor=request.roles.email,
        member=member.email,
        detail=f"{color.title()} strike removed by {request.roles.email}" + (f": {reason}" if reason else ""),
    )
    messages.success(request, f"{color.title()} strike removed from {member.name}.")
    return redirect("portal-admin")


@secretary_or_admin_required
@require_POST
def remove_from_team(request):
    """
    Deactivate a team member — sets active=False rather than deleting the
    row, so points/strikes/task history survive and it's reversible.
    Secretary/admin only.
    """
    removable = [m for m in TeamMember.objects.all() if m.active]
    form = RemoveFromTeamForm(request.POST, removable=removable)
    if not form.is_valid():
        messages.error(request, "Pick an active member to remove.")
        return redirect("portal-admin")

    member = get_object_or_404(TeamMember, email=form.cleaned_data["member_email"])
    if not member.active:
        messages.error(request, f"{member.name} is already inactive.")
        return redirect("portal-admin")

    reason = form.cleaned_data.get("reason", "").strip()
    was_head_of = member.domain_head_of
    member.active = False
    member.domain_head_of = ""  # an inactive member can't stay listed as a vertical's head
    member.save(update_fields=["active", "domain_head_of"])

    from core.activity import log_activity

    detail = f"Removed from team by {request.roles.email}"
    if was_head_of:
        detail += f" (was head of {was_head_of})"
    if reason:
        detail += f": {reason}"
    log_activity("removed-from-team", actor=request.roles.email, member=member.email, detail=detail)
    messages.success(request, f"{member.name} removed from the team.")

    # They lose access immediately (core/roles.py ignores inactive members), so
    # anything still on their plate can no longer be done by them.
    open_work = (
        Task.objects.filter(email__iexact=member.email)
        .exclude(status__in=[TaskStatus.DONE, TaskStatus.UNFILLED])
        .exclude(request__status__in=RequestStatus.TERMINAL)
        .values_list("ref_code", "task")
    )
    if open_work:
        listed = ", ".join(f"{ref} {name}" for ref, name in open_work)
        messages.warning(
            request, f"{member.name} still holds open tasks — reassign them from Assignments: {listed}."
        )
    return redirect("portal-admin")


@secretary_or_admin_required
@require_POST
def committee_manage(request):
    form = CommitteeForm(request.POST)
    if form.is_valid():
        created = not Committee.objects.filter(email=form.cleaned_data["email"].lower()).exists()
        form.save()
        from core.activity import log_activity

        log_activity(
            "committee",
            actor=request.roles.email,
            detail=f"{'added' if created else 'updated'} committee {form.cleaned_data['name']} ({form.cleaned_data['email']})",
        )
        messages.success(request, f"Committee {'added' if created else 'updated'}.")
    else:
        messages.error(request, "Check the committee fields and try again.")
    return redirect("portal-admin")


@secretary_or_admin_required
@require_POST
def team_import(request):
    form = TeamImportForm(request.POST, request.FILES)
    if not form.is_valid():
        for error in form.errors.get("__all__", []):
            messages.error(request, error)
        return redirect("portal-admin")

    rows = parse_csv(form.cleaned_data["csv_text"])
    if not rows:
        messages.error(request, "Nothing to import — the CSV needs a header row plus at least one data row.")
        return redirect("portal-admin")

    results, summary = import_rows(rows)
    messages.success(
        request,
        f"Import complete: {summary['created']} created, {summary['updated']} updated, "
        f"{summary['errors']} error(s).",
    )
    for row in results:
        if row.status == "error":
            messages.error(request, f"{row.email or '(blank)'}: {row.message}")
    return redirect("portal-admin")


@secretary_or_admin_required
@require_POST
def set_vertical_head(request):
    form = VerticalHeadForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Pick a member.")
        return redirect("portal-admin")

    email = form.cleaned_data["member_email"].strip().lower()
    vertical = form.cleaned_data["vertical"]
    member = get_object_or_404(TeamMember, email=email)

    from core.activity import log_activity

    with transaction.atomic():
        if vertical:
            # One head per vertical — demote whoever currently holds it.
            TeamMember.objects.filter(domain_head_of=vertical).exclude(pk=member.pk).update(
                domain_head_of=""
            )
            member.domain_head_of = vertical
            # A head belongs to the vertical they head. If they already work in
            # it — as their primary or their secondary — leave their verticals
            # exactly as they are; only otherwise make it their primary.
            if not member.in_vertical(vertical):
                member.vertical = vertical
        else:
            member.domain_head_of = ""
        member.save(update_fields=["domain_head_of", "vertical"])

    log_activity(
        "domain-head",
        actor=request.roles.email,
        member=email,
        detail=f"Made head of {vertical}" if vertical else "Removed as head",
    )
    messages.success(request, "Vertical head updated.")
    return redirect("portal-admin")


def _back_to_roster(request):
    """
    Return to the Admin page after a roster-row action, keeping whatever filters
    were applied (the row forms send the current URL as `next`). Only a path on
    this site's own Admin page is honoured, so it can't be used to redirect anywhere else.
    """
    target = request.POST.get("next", "")
    if target.startswith("/portal-admin/") and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}
    ):
        return redirect(target)
    return redirect("portal-admin")


@secretary_or_admin_required
@require_POST
def set_availability(request):
    form = AvailabilityForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Pick a member and a status.")
        return _back_to_roster(request)

    email = form.cleaned_data["member_email"].strip().lower()
    next_status = form.cleaned_data["availability"]

    # The POC/Admin can always mark someone out or back directly, no request
    # needed. The shared function does the locked read-modify-write and the
    # day-banking, so this and the member-driven paths can't drift apart.
    get_object_or_404(TeamMember, email=email)
    member = switch_availability(email, next_status, request.roles.email)
    messages.success(request, f"{member.name} marked {'out of work' if next_status == 'out' else 'on work'}.")
    return _back_to_roster(request)


@secretary_or_admin_required
@require_POST
def set_member_phone(request):
    """
    Secretary/admin updating a team member's contact number from the master
    roster. Clubs see this number once the member is assigned to their
    request — request_detail.html's roster cards and the acceptance email
    (engine/confirm.py's `_roster_email`) both already read `Task.phone`,
    itself copied from this field when the task is created.
    """
    form = MemberPhoneForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Enter a valid member and phone number.")
        return _back_to_roster(request)

    email = form.cleaned_data["member_email"].strip().lower()
    member = get_object_or_404(TeamMember, email=email)
    member.phone = form.cleaned_data["phone"].strip()
    member.save(update_fields=["phone"])
    messages.success(request, f"Updated {member.name}'s contact number.")
    return _back_to_roster(request)


@secretary_or_admin_required
@require_POST
def set_member_verticals(request):
    """
    Secretary/admin setting a member's primary and secondary vertical from the
    master roster. Primary is tried first by auto-assignment, secondary second;
    a domain head's scope covers members in either.

    The seed command re-applies verticals from core/seed_data.py on every
    deploy, so a change that should survive the next deploy has to be made
    there too — the message says so.
    """
    form = MemberVerticalsForm(request.POST)
    if not form.is_valid():
        messages.error(request, " ".join(form.errors.get("__all__", [])) or "Pick valid verticals.")
        return _back_to_roster(request)

    email = form.cleaned_data["member_email"].strip().lower()
    member = get_object_or_404(TeamMember, email=email)
    old = member.vertical_label
    member.vertical = form.cleaned_data["vertical"]
    member.secondary_vertical = form.cleaned_data["secondary_vertical"]
    member.save(update_fields=["vertical", "secondary_vertical"])

    from core.activity import log_activity

    log_activity(
        "verticals-changed",
        actor=request.roles.email,
        member=member.email,
        detail=f"{old or '(none)'} -> {member.vertical_label or '(none)'}",
    )
    messages.success(
        request,
        f"Updated {member.name}'s verticals. (The next deploy re-applies the seed file, "
        "so make the same change in core/seed_data.py to keep it.)",
    )
    return _back_to_roster(request)


@login_required
@require_POST
def point_scheme(request):
    if not request.roles.is_admin:
        raise PermissionDenied("Only admins can change the point scheme.")

    scheme = PointsScheme.load()
    before = {f.name: getattr(scheme, f.name) for f in PointsScheme._meta.fields if f.name != "id"}
    form = PointSchemeForm(request.POST, instance=scheme)
    if form.is_valid():
        form.save()
        from core.activity import log_activity

        # Say what changed, so the audit log answers "who changed the points, and to what".
        changed = [
            f"{form.fields[name].label or name}: {before[name]} -> {getattr(scheme, name)}"
            for name in before
            if before[name] != getattr(scheme, name)
        ]
        log_activity(
            "points-scheme",
            actor=request.roles.email,
            detail="Point scheme updated: " + ("; ".join(changed) if changed else "no change"),
        )
        messages.success(request, "Point scheme saved." if changed else "No changes to save.")
    else:
        # Name the actual problem(s) instead of a generic "check the values".
        problems = [
            f"{form.fields[name].label if name in form.fields else 'Scheme'}: {errors[0]}"
            for name, errors in form.errors.items()
        ]
        messages.error(request, "Point scheme not saved. " + " ".join(problems[:3]))
    return redirect("portal-admin")
