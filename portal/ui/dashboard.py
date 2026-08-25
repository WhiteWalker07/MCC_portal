"""
Dashboard aggregates (docs/PRD.md §5.11).

Ported from `server/src/routes/dashboard.ts`. Reads team, tasks and requests and
returns pre-computed stats, optionally sliced by period / campus / year /
vertical. The old route took filters as a POST body from a JS fetch; here they
arrive as GET query parameters, which is what makes the dashboard bookmarkable
and shareable as a plain URL — a small upgrade the move to server rendering
buys for free.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from django.utils import timezone

from core.constants import TaskStatus
from core.models import Request, Task, TeamMember

HOUR = 3600


def _period_cutoff(period: str) -> datetime | None:
    now = timezone.now()
    if period == "7d":
        return now - timedelta(days=7)
    if period == "30d":
        return now - timedelta(days=30)
    if period == "90d":
        return now - timedelta(days=90)
    if period == "month":
        return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return None  # "all"


def compute_stats(filters: dict) -> dict:
    period = filters.get("period", "all")
    campus = filters.get("campus", "all")
    year = str(filters.get("year", "all"))
    vertical = filters.get("vertical", "all")
    cutoff = _period_cutoff(period)
    member_filtered = campus != "all" or year != "all" or vertical != "all"

    members = list(TeamMember.objects.all())
    member_by_email = {m.email.lower(): m for m in members}

    def member_matches(member: TeamMember | None) -> bool:
        if member is None:
            return False
        if campus != "all" and (member.campus or "") != campus:
            return False
        if year != "all" and str(member.year or "") != year:
            return False
        if vertical != "all" and (member.vertical or "") != vertical:
            return False
        return True

    filtered_members = [m for m in members if member_matches(m)]

    done_by_email: dict[str, int] = {}
    on_time_by_email: dict[str, int] = {}
    completed = on_time = open_late = 0
    turnaround_sum = 0.0
    turnaround_n = 0

    for task in Task.objects.all():
        email = (task.email or "").lower()
        if member_filtered and not member_matches(member_by_email.get(email)):
            continue

        if task.status == TaskStatus.LATE:
            open_late += 1
        if task.status != TaskStatus.DONE:
            continue

        completed_at = task.completed_at
        if cutoff and (completed_at is None or completed_at < cutoff):
            continue

        completed += 1
        if email:
            done_by_email[email] = done_by_email.get(email, 0) + 1

        punctual = True
        if task.deadline and completed_at:
            punctual = completed_at <= task.deadline
        if punctual:
            on_time += 1
            if email:
                on_time_by_email[email] = on_time_by_email.get(email, 0) + 1

        if task.created_at and completed_at and completed_at >= task.created_at:
            turnaround_sum += (completed_at - task.created_at).total_seconds()
            turnaround_n += 1

    leaderboard = sorted(
        (
            {
                "name": m.name or m.email,
                "email": m.email,
                "vertical": m.vertical or "",
                "campus": m.campus or "",
                "points": m.points or 0,
                "strikes": m.strikes or 0,
                "active": m.active,
                "done": done_by_email.get(m.email.lower(), 0),
                "on_time_pct": (
                    round(100 * on_time_by_email.get(m.email.lower(), 0) / done_by_email[m.email.lower()])
                    if done_by_email.get(m.email.lower())
                    else None
                ),
            }
            for m in filtered_members
        ),
        key=lambda row: (-row["points"], -row["done"]),
    )

    by_vertical_map: dict[str, dict] = {}
    for m in filtered_members:
        v = m.vertical or "—"
        entry = by_vertical_map.setdefault(v, {"points": 0, "members": 0, "done": 0})
        entry["points"] += m.points or 0
        entry["members"] += 1
    for email, count in done_by_email.items():
        member = member_by_email.get(email)
        v = (member.vertical if member else "") or "—"
        if v in by_vertical_map:
            by_vertical_map[v]["done"] += count
    by_vertical = sorted(
        ({"vertical": v, **stats} for v, stats in by_vertical_map.items()),
        key=lambda row: -row["points"],
    )

    requests_by_status: dict[str, int] = {}
    total_requests = 0
    for req in Request.objects.all():
        if campus != "all" and (req.campus or "") != campus:
            continue
        if cutoff and (req.created_at is None or req.created_at < cutoff):
            continue
        status = req.status or "New"
        requests_by_status[status] = requests_by_status.get(status, 0) + 1
        total_requests += 1

    return {
        "filters": {"period": period, "campus": campus, "year": year, "vertical": vertical},
        "totals": {
            "active_members": sum(1 for m in filtered_members if m.active),
            "total_members": len(filtered_members),
            "total_points": sum(m.points or 0 for m in filtered_members),
            "tasks_completed": completed,
            "on_time_rate": round(100 * on_time / completed) if completed else None,
            "avg_turnaround_hours": (
                round(turnaround_sum / turnaround_n / HOUR, 1) if turnaround_n else None
            ),
            "open_late": open_late,
            "total_requests": total_requests,
        },
        "leaderboard": leaderboard,
        "by_vertical": by_vertical,
        "requests_by_status": requests_by_status,
    }
