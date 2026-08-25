"""
Reference-code and campus allocation (docs/PRD.md §5.1).

Every request gets a human-readable code: `ACRONYM_n` from the requesting
committee's own counter, or `MEDIA_n` from a shared counter for a Post raised by
an account that isn't a registered committee. Codes must never collide and never
skip, even under concurrent submissions.

The Mongo original got that from an atomic `findOneAndUpdate({$inc})`. Here the
equivalent is an `UPDATE ... SET last_seq = last_seq + 1` inside a transaction:
the increment happens in the database, not in Python, so two simultaneous
submissions can't read the same value. SQLite is configured with
`transaction_mode="IMMEDIATE"` (see settings.py), which takes the write lock at
BEGIN — so the read-back below is guaranteed to see this transaction's own
increment and nobody else's.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from django.db import transaction
from django.db.models import F

from core.models import Committee, PortalSettings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Allocation:
    ok: bool
    ref_code: str = ""
    campus: str = ""
    skipped: bool = False


@transaction.atomic
def allocate_ref_code(request_obj) -> Allocation:
    """
    Assign `ref_code` and `campus` to a request and save them.

    Idempotent: a request that already has a code is left alone, so a retried
    call can't burn a second sequence number.
    """
    if request_obj.ref_code:
        return Allocation(ok=False, skipped=True)

    email = (request_obj.contact_email or "").strip().lower()

    committee = Committee.objects.filter(email=email).first()
    if committee is not None:
        Committee.objects.filter(pk=committee.pk).update(last_seq=F("last_seq") + 1)
        committee.refresh_from_db(fields=["last_seq"])
        ref_code = f"{committee.acronym}_{committee.last_seq}"
        campus = committee.campus or ""
    else:
        # A general institute requester. Coverage is reserved to committees by
        # the view, so this is a Post request; campus is left blank.
        settings_row = PortalSettings.load()
        PortalSettings.objects.filter(pk=settings_row.pk).update(general_seq=F("general_seq") + 1)
        settings_row.refresh_from_db(fields=["general_seq"])
        ref_code = f"{settings_row.default_acronym or 'MEDIA'}_{settings_row.general_seq}"
        campus = ""

    request_obj.ref_code = ref_code
    request_obj.campus = campus
    request_obj.coordinator_email = ""
    request_obj.save(update_fields=["ref_code", "campus", "coordinator_email"])
    return Allocation(ok=True, ref_code=ref_code, campus=campus)
