"""Caregiver memory synchronisation.

The backend's own database (family, medicines, routines) is the source of
truth; this endpoint lets it push the current caregiver-provided dataset for
one patient into the VoiceBot's existing memory store. It is a full replace
per call, not a merge — see sync_caregiver_memory's docstring for exactly
which rows that touches and which it never touches.

No new memory architecture, no new database, no parallel model: this maps
directly onto the existing FamilyMember/Medicine/DailyRoutine models and the
existing provenance system (source='caregiver', verification_status
='verified' — the same trust level a caregiver's write already has anywhere
else in this codebase).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...app import Application
from ...logging import get_logger
from ...memory.models import DailyRoutine, FamilyMember, Medicine
from ...memory.provenance import provenance_for_write
from ...memory.repository import CAREGIVER_SYNC_MARKER
from ...schemas import MemorySyncRequest, MemorySyncResponse
from ..dependencies import application, authorized_user_ids, ensure_patient_active, require_api_key

log = get_logger('api.memory_sync')

router = APIRouter(tags=['memory'], dependencies=[Depends(require_api_key)])

SYNC_SOURCE = 'caregiver'


@router.post('/v1/memory/sync', response_model=MemorySyncResponse)
def memory_sync(
    payload: MemorySyncRequest,
    app: Application = Depends(application),
    authorized: frozenset = Depends(authorized_user_ids),
) -> MemorySyncResponse:
    """Full-snapshot, atomic replace of this patient's caregiver-sourced
    family/medicine/routine rows -- per category, an empty array clears
    that category's caregiver-sourced rows, a non-empty array replaces
    them, and rows from any other source (the patient's own words, an
    assistant note, seed/import data) are never touched. Auto-provisions
    the patient (idempotent, never overwrites an existing display name) if
    this is the first contact for that ``user_id``. There is currently no
    revision/version field on this request -- every call is a full,
    unconditional replace; see the integration-readiness notes for why
    that is deliberate pending a backend contract decision, not an
    oversight."""
    if payload.user_id not in authorized:
        raise HTTPException(403, 'user_id is not authorized for this API credential')
    ensure_patient_active(app, payload.user_id)

    provenance = provenance_for_write(source=SYNC_SOURCE, created_by=CAREGIVER_SYNC_MARKER)

    # phone_available is accepted for schema completeness but deliberately
    # never persisted: this endpoint must never store a dialable number, and
    # there is no existing field to record "a number exists somewhere else."
    # A family member synced only through this endpoint therefore cannot be
    # called via the voice assistant until a real phone number is added
    # through the existing, separate, more privileged channel for that.
    family_members = [
        FamilyMember(user_id=payload.user_id, name=fm.name, relation=fm.relationship,
                    phone=None, is_trusted_contact=False, is_primary_contact=False,
                    provenance=provenance)
        for fm in payload.family_members
    ]
    medicines = [
        Medicine(user_id=payload.user_id, name=m.name, dosage=m.dose,
                instructions=m.schedule, provenance=provenance)
        for m in payload.medicines
    ]
    routines = [
        DailyRoutine(user_id=payload.user_id, title=r.activity, routine_time=r.time,
                    provenance=provenance)
        for r in payload.daily_routines
    ]

    try:
        family_count, medicine_count, routine_count = app.memory.repo.sync_caregiver_memory(
            payload.user_id, family_members=family_members, medicines=medicines,
            routines=routines)
    except Exception as exc:
        log.error('memory_sync_failed', fields={'user_id': payload.user_id,
                                                 'error': type(exc).__name__})
        raise HTTPException(500, 'Memory synchronization failed') from exc

    return MemorySyncResponse(
        success=True, user_id=payload.user_id,
        family_members_synced=family_count,
        medicines_synced=medicine_count,
        daily_routines_synced=routine_count)
