"""
The landing page: where signing in lands you.

One Home for everyone, with sections that depend on who you are. A club sees its
requests and a New Request button. A team member also sees their open tasks (the
overdue ones first), upcoming meetings and their standing. The POC/Admin also see
what needs a decision or attention: approvals waiting, overdue tasks, roles nobody
could be found for, and the events coming up in the next week. The statistics
Dashboard (leaderboard, filters) is a separate page.

Everything here is read-only and scoped to the signed-in account; nothing on the
page is a control that changes data except links to the pages that do.
"""

from __future__ import annotations

from datetime import timedelta

from django.shortcuts import render
from django.utils import timezone

from core.constants import Availability, LeaveStatus, RequestStatus, RequestType, TaskStatus
from core.models import LeaveRequest, Meeting, Request, Task

#: How many rows each list on the page shows (each has a link to see the rest).
LIST_LIMIT = 5
#: How far ahead "events coming up" looks.
UPCOMING_DAYS = 7


def home(request):
    if not request.user.is_authenticated:
        return render(request, "ui/signin.html")

    roles = request.roles
    now = timezone.now()
    live = Request.objects.exclude(status__in=RequestStatus.TERMINAL)
    context: dict = {"now": now, "member": roles.member}

    # Everyone: requests raised by this account.
    mine = Request.objects.filter(contact_email=roles.email)
    context["my_requests"] = list(mine[:LIST_LIMIT])
    context["my_open_requests"] = mine.exclude(status__in=RequestStatus.TERMINAL).count()

    if roles.member is not None:
        member = roles.member
        tasks = (
            Task.objects.filter(email=roles.email, status__in=[TaskStatus.CONFIRMED, TaskStatus.LATE])
            .exclude(task="Task Supervisor")
            .select_related("request", "sub_event")
            .order_by("deadline")
        )
        # Overdue first, then soonest due.
        ordered = sorted(tasks, key=lambda t: (t.status != TaskStatus.LATE, t.deadline is None, t.deadline or now))
        context.update(
            {
                "my_tasks": ordered[:LIST_LIMIT],
                "my_task_count": len(ordered),
                "my_late_count": sum(1 for t in ordered if t.status == TaskStatus.LATE),
                "is_out": member.availability == Availability.OUT,
                "pending_leave": LeaveRequest.objects.filter(member=member, status=LeaveStatus.PENDING).first(),
                "my_meetings": list(
                    Meeting.objects.filter(
                        invites__member=member, cancelled_at__isnull=True, end__gte=now
                    ).order_by("start")[:3]
                ),
            }
        )

    # Requests this account coordinates or supervises, while they are still running.
    coordinating = live.filter(coordinator_email=roles.email) if roles.is_team else live.none()
    supervising = live.filter(supervisor_email=roles.email) if roles.is_team else live.none()
    context["coordinating"] = list(coordinating[:LIST_LIMIT])
    context["supervising"] = list(supervising[:LIST_LIMIT])

    if roles.is_domain_head:
        context["unfilled_in_vertical"] = (
            Task.objects.filter(status=TaskStatus.UNFILLED, vertical=roles.domain_head_of)
            .exclude(request__status__in=RequestStatus.TERMINAL)
            .count()
        )

    if roles.is_staff_side:
        pending_requests = Request.objects.filter(status=RequestStatus.PENDING).count()
        pending_leaves = LeaveRequest.objects.filter(status=LeaveStatus.PENDING).count()
        context.update(
            {
                "is_staff": True,
                "pending_requests": pending_requests,
                "pending_leaves": pending_leaves,
                "late_tasks": Task.objects.filter(status=TaskStatus.LATE)
                .exclude(request__status__in=RequestStatus.TERMINAL)
                .count(),
                "unfilled_tasks": Task.objects.filter(status=TaskStatus.UNFILLED)
                .exclude(request__status__in=RequestStatus.TERMINAL)
                .count(),
                "upcoming_events": list(
                    Request.objects.filter(
                        type=RequestType.COVERAGE,
                        event_start__gte=now,
                        event_start__lte=now + timedelta(days=UPCOMING_DAYS),
                    )
                    .exclude(status__in=[RequestStatus.REJECTED])
                    .order_by("event_start")[: LIST_LIMIT * 2]
                ),
                "upcoming_days": UPCOMING_DAYS,
            }
        )
    return render(request, "ui/home.html", context)
