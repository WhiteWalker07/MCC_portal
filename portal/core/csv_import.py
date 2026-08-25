"""
Team roster CSV import (docs/PRD.md §5.10).

Ported from `routes/team.ts`'s `importTeamCsv` handler and the parser in
`web/js/data.js`. Re-importing updates existing members without resetting their
points or strikes — only a brand-new row starts at zero.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from django.db import transaction

from core.models import TeamMember


@dataclass
class RowResult:
    email: str
    status: str  # "created" | "updated" | "error"
    message: str = ""


def _parse_bool(value: str, default: bool) -> bool:
    text = (value or "").strip().lower()
    if not text:
        return default
    return text in {"true", "1", "yes", "y"}


def parse_csv(text: str) -> list[dict]:
    """Header row + comma-separated fields. Skills are ';'-separated within a cell."""
    reader = csv.DictReader(io.StringIO(text.strip()))
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def import_rows(rows: list[dict]) -> tuple[list[RowResult], dict]:
    results: list[RowResult] = []

    for raw in rows:
        email = (raw.get("email") or "").strip().lower()
        if not email or "@" not in email:
            results.append(RowResult(email=raw.get("email", ""), status="error", message="invalid email"))
            continue

        name = (raw.get("name") or "").strip()
        if not name:
            results.append(RowResult(email=email, status="error", message="name required"))
            continue

        skills = [s.strip() for s in (raw.get("skills") or "").replace(",", ";").split(";") if s.strip()]
        data = {
            "name": name,
            "vertical": (raw.get("vertical") or "").strip(),
            "year": int(raw.get("year") or 1) if str(raw.get("year") or "").strip().isdigit() else 1,
            "domain_head_of": (raw.get("domainHeadOf") or raw.get("domain_head_of") or "").strip(),
            "skills": skills,
            "campus": (raw.get("campus") or "").strip(),
            "phone": (raw.get("phone") or "").strip(),
            "active": _parse_bool(raw.get("active"), True),
        }

        try:
            with transaction.atomic():
                existing = TeamMember.objects.filter(email=email).first()
                if existing:
                    for field, value in data.items():
                        setattr(existing, field, value)
                    existing.save()
                    results.append(RowResult(email=email, status="updated"))
                else:
                    TeamMember.objects.create(email=email, points=0, strikes=0, **data)
                    results.append(RowResult(email=email, status="created"))
        except Exception as exc:
            results.append(RowResult(email=email, status="error", message=str(exc)))

    summary = {
        "created": sum(1 for r in results if r.status == "created"),
        "updated": sum(1 for r in results if r.status == "updated"),
        "errors": sum(1 for r in results if r.status == "error"),
    }
    return results, summary
