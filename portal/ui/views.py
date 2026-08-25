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

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import F
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from core.constants import RequestStatus, RequestType, TaskStatus
from core.csv_import import import_rows, parse_csv
from core.decorators import secretary_or_admin_required, team_required
from core.models import (
    Committee,
    PointsScheme,
    Request,
    Task,
    TaskType,
    TeamMember,
)
from core.roles import can_assign, can_edit_venue, can_read_request, can_strike
from engine.assign import eligible_members
from engine.assignment import perform_swap, validate_member
from engine.confirm import confirm_request
from engine.notify import award_points
from engine.pipeline import compute_deadline
from engine.workflow import complete_task, process_new_request, reject_request, schedule_posts
from services.calendar import calendar_service

from . import dashboard as dashboard_data
from .forms import (
    AddTaskForm,
    AvailabilityForm,
    CommitteeForm,
    PointSchemeForm,
    ReassignForm,
    RejectForm,
    RemoveFromTeamForm,
    RemoveStrikeForm,
    RequestForm,
    StrikeForm,
    TeamImportForm,
    VenueEditForm,
    VerticalHeadForm,
)

DAY_SECONDS = 86400


# ── Home / sign-in ───────────────────────────────────────────────────────────


def home(request):
    if request.user.is_authenticated:
        return redirect("request-list")
    return render(request, "ui/signin.html")


def signed_out(request):
    """Landing spot after a domain refusal or an explicit sign-out."""
    return render(request, "ui/signin.html")


# ── Requests ─────────────────────────────────────────────────────────────────


@login_required
def request_new(request):
    roles = request.roles
    is_committee = roles.committee is not None
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
        )
        if form.is_valid():
            new_request = form.save(commit=False)
            # Content fields only — everything engine-owned is set here, never
            # taken from the form.
            new_request.contact_email = roles.email
            new_request.status = RequestStatus.NEW
            new_request.created_at = timezone.now()
            new_request.roles_needed = form.cleaned_data.get("roles_needed") or []
            new_request.platforms = form.cleaned_data.get("platforms") or []
            new_request.save()

            process_new_request(new_request)
            messages.success(request, f"Request {new_request.ref_code or ''} submitted.")
            return redirect("request-detail", pk=new_request.pk)
    else:
        initial = {"requester": roles.committee.name if is_committee else (request.user.get_full_name() or roles.email)}
        form = RequestForm(
            is_committee=is_committee,
            available_roles=available_roles,
            available_platforms=available_platforms,
            initial=initial,
        )

    return render(
        request,
        "ui/request_new.html",
        {"form": form, "is_committee": is_committee},
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
        and request_obj.status != RequestStatus.REJECTED
        and can_edit_venue(request.roles, request_obj)
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
    if not task.is_completable:
        messages.error(request, "That task isn't open for completion.")
        return redirect("task-list")
    if task.awaits_event:
        messages.error(request, "You can't mark this done until the event has started.")
        return redirect("task-list")

    task.status = TaskStatus.DONE
    task.completed_at = timezone.now()
    task.save(update_fields=["status", "completed_at"])
    complete_task(task)
    messages.success(request, f"{task.task} marked done.")
    return redirect("task-list")


# ── Assignments ──────────────────────────────────────────────────────────────


@login_required
def assignment_list(request):
    roles = request.roles
    if not roles.can_reach_assignments:
        raise PermissionDenied("You don't have an assignment role.")

    if roles.is_staff_side or roles.is_second_year:
        task_qs = Task.objects.all()
    else:
        task_qs = Task.objects.filter(coordinator_email=roles.email)
        if roles.domain_head_of:
            task_qs = task_qs | Task.objects.filter(vertical=roles.domain_head_of)
        task_qs = task_qs.distinct()

    requests = (
        Request.objects.filter(pk__in=task_qs.values_list("request_id", flat=True))
        .exclude(status__in=RequestStatus.TERMINAL)
        .prefetch_related("tasks")
    )

    # Secretary/admin/domain-head all land here already, which is why the
    # manual-strike form lives on this page rather than /portal-admin/ (which
    # a domain head can't reach at all).
    strikeable = [m for m in TeamMember.objects.all() if can_strike(roles, m)]
    strike_form = StrikeForm(strikeable=strikeable) if strikeable else None

    return render(
        request,
        "ui/assignment_list.html",
        {"requests": requests, "strike_form": strike_form},
    )


@login_required
@require_POST
def issue_strike(request):
    """
    Manually add one strike to a team member (docs/PRD.md §5.7) — a deliberate
    human judgment call, separate from the automated deadline sweep that's
    otherwise the only source of strikes. Scoped by `can_strike`: secretary/
    admin may strike anyone, a domain head only their own vertical.
    """
    strikeable = [m for m in TeamMember.objects.all() if can_strike(request.roles, m)]
    form = StrikeForm(request.POST, strikeable=strikeable)
    if not form.is_valid():
        messages.error(request, "Pick a member to strike.")
        return redirect("assignment-list")

    member = get_object_or_404(TeamMember, email=form.cleaned_data["member_email"])
    if not can_strike(request.roles, member):
        raise PermissionDenied("Not allowed to strike this member.")

    reason = form.cleaned_data.get("reason", "").strip()
    TeamMember.objects.filter(pk=member.pk).update(strikes=F("strikes") + 1)

    from core.activity import log_activity

    log_activity(
        "strike",
        actor=request.roles.email,
        member=member.email,
        detail=f"Manual strike by {request.roles.email}" + (f": {reason}" if reason else ""),
    )
    messages.success(request, f"Strike issued to {member.name}.")
    return redirect("assignment-list")


@login_required
def assignment_detail(request, pk):
    roles = request.roles
    request_obj = get_object_or_404(Request, pk=pk)
    tasks = list(request_obj.tasks.all())
    settings = _settings()
    team = _team()

    task_rows = []
    for task in tasks:
        manageable = can_assign(roles, task.vertical, request_obj.coordinator_email)
        form = None
        if manageable:
            # The dropdown deliberately spans the whole active team, not just
            # this task's vertical/skill — a coordinator may need to pull
            # someone in from elsewhere as a stopgap. require_skill=False only
            # relaxes that one check; active/strikes/campus/calendar still
            # apply. Auto-pick (leaving the dropdown on its default) stays
            # skill-matched — see assignment_reassign.
            eligible = eligible_members(
                task.required_skill,
                task.at_event,
                request_obj,
                settings,
                team,
                calendar_service(),
                exclude={task.email} if task.email else set(),
                require_skill=False,
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
        if not can_assign(roles, task_type.vertical, request_obj.coordinator_email):
            continue
        eligible = eligible_members(
            task_type.required_skill,
            task_type.at_event,
            request_obj,
            settings,
            team,
            calendar_service(),
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

    if not can_assign(roles, task.vertical, request_obj.coordinator_email):
        raise PermissionDenied("Not allowed to reassign this task.")

    settings = _settings()
    exclude = {task.email} if task.email else set()

    # The manual-pick pool spans the whole team (any vertical) — the form must
    # be bound against the same broad list it was rendered with, or a
    # cross-vertical pick fails validation as "not a valid choice".
    any_vertical = eligible_members(
        task.required_skill, task.at_event, request_obj, settings, _team(),
        calendar_service(), exclude, require_skill=False,
    )
    form = ReassignForm(request.POST, eligible=any_vertical)
    if not form.is_valid():
        messages.error(request, "That selection isn't valid — try again.")
        return redirect("assignment-detail", pk=request_obj.pk)

    if form.mode == ReassignForm.MODE_MANUAL:
        # A deliberate hand-pick may cross verticals; every other eligibility
        # rule (active, strikes, campus, calendar) still applies.
        validation = validate_member(
            form.cleaned_data["member_email"],
            task.required_skill,
            task.at_event,
            request_obj,
            settings,
            require_skill=False,
        )
        if not validation.ok:
            messages.error(request, validation.reason)
            return redirect("assignment-detail", pk=request_obj.pk)
        member = validation.member
    else:
        # Left on "auto-pick": stay skill-matched, so the engine doesn't
        # silently hand a photography task to a videographer just because
        # nobody chose anyone.
        skill_matched = eligible_members(
            task.required_skill, task.at_event, request_obj, settings, _team(),
            calendar_service(), exclude,
        )
        if not skill_matched:
            messages.error(request, f'No eligible member with "{task.required_skill}".')
            return redirect("assignment-detail", pk=request_obj.pk)
        member = skill_matched[0]

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
    if not can_assign(roles, task_type.vertical, request_obj.coordinator_email):
        raise PermissionDenied("Not allowed to add this task.")

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
        task_type.required_skill, task_type.at_event, request_obj, settings, _team(), calendar_service(), exclude
    )
    form = AddTaskForm(request.POST, task_type=task_type.task, eligible=eligible)
    if not form.is_valid():
        messages.error(request, "That selection isn't valid — try again.")
        return redirect("assignment-detail", pk=request_obj.pk)

    member = _resolve_member(form, eligible, task_type.required_skill, task_type.at_event, request_obj, settings)
    if member is None:
        messages.error(request, f'No eligible member with "{task_type.required_skill}".')
        return redirect("assignment-detail", pk=request_obj.pk)

    if existing_coordinator is not None:
        perform_swap(existing_coordinator, member, request_obj)
        messages.success(request, f"Event Coordinator reassigned to {member.name}.")
        return redirect("assignment-detail", pk=request_obj.pk)

    confirmed_state = request_obj.status in RequestStatus.CONFIRMED_STATES
    with transaction.atomic():
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
            points=task_type.points,
            points_awarded=confirmed_state,
            deadline=compute_deadline(task_type, request_obj, timezone.now()),
            status=TaskStatus.CONFIRMED if confirmed_state else TaskStatus.PROPOSED,
            coordinator_email=request_obj.coordinator_email or "",
            event_name=request_obj.event_name or "",
            event_start=request_obj.event_start,
            event_end=request_obj.event_end,
            venue=request_obj.venue or "",
        )
        if confirmed_state:
            award_points(member.email, task_type.points)

    from engine.notify import notify_assignee
    from core.activity import log_activity

    notify_assignee(new_task)
    log_activity(
        "manual-assign",
        request_obj=request_obj,
        ref_code=request_obj.ref_code,
        member=member.email,
        detail=f"{task_type.task} -> {member.name} ({form.mode})",
    )
    messages.success(request, f"{task_type.task} added, assigned to {member.name}.")
    return redirect("assignment-detail", pk=request_obj.pk)


def _resolve_member(form, eligible, required_skill, at_event, request_obj, settings):
    """Validate a manual pick against the real rules, or take the top of `eligible`."""
    if form.mode == ReassignForm.MODE_MANUAL:
        validation = validate_member(
            form.cleaned_data["member_email"], required_skill, at_event, request_obj, settings
        )
        return validation.member if validation.ok else None
    return eligible[0] if eligible else None


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

    request_obj.status = RequestStatus.READY_TO_POST
    request_obj.ready_by = roles.email
    request_obj.ready_at = timezone.now()
    request_obj.save(update_fields=["status", "ready_by", "ready_at"])
    schedule_posts(request_obj)
    messages.success(request, "Marked ready to post — scheduling in progress.")
    return redirect("request-detail", pk=request_obj.pk)


# ── Approvals ────────────────────────────────────────────────────────────────


@secretary_or_admin_required
def approval_list(request):
    pending = Request.objects.filter(status=RequestStatus.PENDING)
    return render(request, "ui/approval_list.html", {"requests": pending})


@secretary_or_admin_required
def approval_detail(request, pk):
    request_obj = get_object_or_404(Request, pk=pk)
    tasks = request_obj.tasks.all()
    return render(
        request,
        "ui/approval_detail.html",
        {"request_obj": request_obj, "tasks": tasks, "reject_form": RejectForm()},
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
        confirm_request(request_obj)
        messages.success(request, f"{request_obj.ref_code} approved.")
    elif decision == "reject":
        form = RejectForm(request.POST)
        reason = form.cleaned_data.get("reason", "") if form.is_valid() else ""
        reject_request(request_obj, reason, request.roles.email)
        messages.success(request, f"{request_obj.ref_code} rejected.")
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

    committees = Committee.objects.all()
    members = TeamMember.objects.all()
    heads = {m.domain_head_of: m for m in members if m.domain_head_of}
    scheme = PointsScheme.load()

    strikeable = [m for m in members if m.strikes > 0]
    removable = [m for m in members if m.active]

    context = {
        "committees": committees,
        "committee_form": CommitteeForm(),
        "members": members,
        "verticals": VERTICALS,
        "heads": heads,
        "team_import_form": TeamImportForm(),
        "point_form": PointSchemeForm(instance=scheme) if request.roles.is_admin else None,
        "is_admin": request.roles.is_admin,
        "remove_strike_form": RemoveStrikeForm(strikeable=strikeable) if strikeable else None,
        "remove_from_team_form": RemoveFromTeamForm(removable=removable) if removable else None,
    }
    return render(request, "ui/admin.html", context)


@secretary_or_admin_required
@require_POST
def remove_strike(request):
    """Waive one strike. Secretary/admin only — see RemoveStrikeForm."""
    strikeable = [m for m in TeamMember.objects.all() if m.strikes > 0]
    form = RemoveStrikeForm(request.POST, strikeable=strikeable)
    if not form.is_valid():
        messages.error(request, "Pick a member with a strike to remove.")
        return redirect("portal-admin")

    member = get_object_or_404(TeamMember, email=form.cleaned_data["member_email"])
    if member.strikes <= 0:
        messages.error(request, f"{member.name} has no strikes to remove.")
        return redirect("portal-admin")

    reason = form.cleaned_data.get("reason", "").strip()
    TeamMember.objects.filter(pk=member.pk).update(strikes=F("strikes") - 1)

    from core.activity import log_activity

    log_activity(
        "strike-removed",
        actor=request.roles.email,
        member=member.email,
        detail=f"Strike removed by {request.roles.email}" + (f": {reason}" if reason else ""),
    )
    messages.success(request, f"Strike removed from {member.name}.")
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


@secretary_or_admin_required
@require_POST
def set_availability(request):
    from core.constants import Availability, DAY

    form = AvailabilityForm(request.POST)
    if not form.is_valid():
        messages.error(request, "Pick a member and a status.")
        return redirect("portal-admin")

    email = form.cleaned_data["member_email"].strip().lower()
    next_status = form.cleaned_data["availability"]
    member = get_object_or_404(TeamMember, email=email)

    now = timezone.now()
    previous = member.availability if member.availability == Availability.OUT else Availability.AVAILABLE
    changed_at = member.availability_changed_at or now
    segment_days = max(0.0, (now - changed_at).total_seconds() / DAY)

    if previous == Availability.OUT:
        member.out_days = round((member.out_days or 0) + segment_days, 1)
    else:
        member.on_work_days = round((member.on_work_days or 0) + segment_days, 1)

    member.availability = next_status
    member.availability_changed_at = now
    member.save(update_fields=["availability", "availability_changed_at", "on_work_days", "out_days"])

    from core.activity import log_activity

    log_activity(
        "availability",
        actor=request.roles.email,
        member=email,
        detail=f"{previous} -> {next_status}",
    )
    messages.success(request, f"{member.name} marked {'out of work' if next_status == 'out' else 'on work'}.")
    return redirect("portal-admin")


@login_required
@require_POST
def point_scheme(request):
    if not request.roles.is_admin:
        raise PermissionDenied("Only admins can change the point scheme.")

    scheme = PointsScheme.load()
    form = PointSchemeForm(request.POST, instance=scheme)
    if form.is_valid():
        form.save()
        from core.activity import log_activity

        log_activity("points-scheme", actor=request.roles.email, detail="Point scheme updated")
        messages.success(request, "Point scheme saved.")
    else:
        messages.error(request, "Check the point-scheme values — they must all be zero or higher.")
    return redirect("portal-admin")
