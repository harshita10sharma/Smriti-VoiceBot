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

Revisioning (source_revision/schema_version) is opt-in: a request that omits
source_revision gets the original, unversioned full-replace behaviour with
no other change. A request that includes it gets stale/no-op/conflict
detection — see MemoryRepository.sync_caregiver_memory and SyncOutcome.
"""
from __future__ import annotations

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from ...app import Application
from ...idempotency import payload_hash
from ...logging import get_logger
from ...memory.models import DailyRoutine, FamilyMember, Medicine
from ...memory.provenance import provenance_for_write
from ...memory.repository import CAREGIVER_SYNC_MARKER
from ...schemas import MemorySyncRequest, MemorySyncResponse
from ..dependencies import application, authorized_user_ids, ensure_patient_active, require_api_key

log = get_logger('api.memory_sync')

router = APIRouter(tags=['memory'], dependencies=[Depends(require_api_key)])

SYNC_SOURCE = 'caregiver'


def _content_hash(payload: MemorySyncRequest) -> str:
    """A hash of everything a revision covers, so 'same revision, different
    content' can be detected even though the whole point of a revision
    number is that its own value doesn't change when content does."""
    return payload_hash(
        payload.model_dump_json(exclude={'source_revision'}, exclude_none=False))


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
    this is the first contact for that ``user_id``.

    ``source_revision`` is optional. Omit it for the original, unversioned
    behaviour (every call applies unconditionally). Provide it to get
    stale/no-op/conflict detection: an older revision than the one already
    applied, or the same revision with different content, returns 409;
    the same revision with identical content is a harmless 200 no-op; a
    newer revision applies and is recorded."""
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
                    external_id=fm.external_id, memory_prompt=fm.memory_prompt,
                    is_deceased=fm.is_deceased, provenance=provenance)
        for fm in payload.family_members
    ]
    medicines = [
        Medicine(user_id=payload.user_id, name=m.name, dosage=m.dose,
                instructions=m.schedule, active=m.active, external_id=m.external_id,
                chosen_time_min=m.chosen_time_min, window_start_min=m.window_start_min,
                window_end_min=m.window_end_min, days_of_week=m.days_of_week,
                provenance=provenance)
        for m in payload.medicines
    ]
    routines = [
        DailyRoutine(user_id=payload.user_id, title=r.activity, routine_time=r.time,
                    external_id=r.external_id, provenance=provenance)
        for r in payload.daily_routines
    ]

    try:
        outcome = app.memory.repo.sync_caregiver_memory(
            payload.user_id, family_members=family_members, medicines=medicines,
            routines=routines, display_name=payload.display_name,
            external_id=payload.external_id, timezone=payload.timezone,
            language_code=payload.language_code,
            source_revision=payload.source_revision, schema_version=payload.schema_version,
            content_hash=_content_hash(payload) if payload.source_revision is not None else None)
    except sqlite3.IntegrityError as exc:
        # The most likely real-world cause: `external_id` (patient, or a
        # family member/medicine/routine within this sync) is already
        # assigned to a *different* user_id -- users.external_id and each
        # per-table (user_id, external_id) pair are uniqueness-constrained
        # at the database level (see database/migrations.py, migration 5).
        # This is a genuine, foreseeable operational scenario (a backend
        # bug or data-migration mistake reusing an id), not an internal
        # error -- report it as a clear conflict, not an opaque 500.
        log.warning('memory_sync_external_id_conflict',
                   fields={'user_id': payload.user_id, 'error': str(exc)})
        raise HTTPException(
            409, 'One or more external_id values in this request are already assigned to a '
                'different patient or record. External ids must be unique per patient '
                '(family/medicine/routine) or globally (the patient external_id itself).'
        ) from exc
    except Exception as exc:
        log.error('memory_sync_failed', fields={'user_id': payload.user_id,
                                                 'error': type(exc).__name__})
        raise HTTPException(500, 'Memory synchronization failed') from exc

    if outcome.status == 'stale':
        raise HTTPException(409, f'source_revision {payload.source_revision} is older than '
                                 f'the currently applied revision {outcome.source_revision}')
    if outcome.status == 'conflict':
        raise HTTPException(409, f'source_revision {payload.source_revision} was already '
                                 'applied with different content (currently applied revision '
                                 f'{outcome.source_revision}) -- use a new, higher revision')

    return MemorySyncResponse(
        success=True, user_id=payload.user_id,
        family_members_synced=outcome.family_members_synced,
        medicines_synced=outcome.medicines_synced,
        daily_routines_synced=outcome.daily_routines_synced,
        status=outcome.status, source_revision=outcome.source_revision)
