"""Text conversation endpoints.

POST /v1/conversation exists so the whole conversational stack — safety,
commands, memory, tools, language following — can be tested without audio.

POST /v1/conversation/welcome is a separate, side-effect-free endpoint for
opening the app: see ConversationManager.welcome for exactly what it does
and does not do.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ...app import Application
from ...idempotency import payload_hash
from ...schemas import ConversationRequest, ConversationResponse, WelcomeRequest, WelcomeResponse
from ..dependencies import (
    application,
    authorized_user_ids,
    ensure_patient_active,
    idempotency_key,
    request_id,
    require_api_key,
)

router = APIRouter(tags=['conversation'], dependencies=[Depends(require_api_key)])


@router.post('/v1/conversation', response_model=ConversationResponse)
def conversation(payload: ConversationRequest,
                 app: Application = Depends(application),
                 rid: str = Depends(request_id),
                 authorized: frozenset = Depends(authorized_user_ids),
                 idem_key: str | None = Depends(idempotency_key)) -> ConversationResponse:
    if payload.user_id not in authorized:
        raise HTTPException(403, 'user_id is not authorized for this API credential')
    ensure_patient_active(app, payload.user_id)
    language = payload.language
    if language:
        if not app.languages.is_known(language):
            raise HTTPException(400, f'Unknown language: {language!r}')
    else:
        language = app.detector.detect(payload.message).language

    # Optional: a client that sent Idempotency-Key gets exactly-once
    # execution for this (user_id, key) pair. A client that sends nothing
    # is completely unaffected (see smriti_voice/idempotency.py).
    if idem_key:
        hash_value = payload_hash(payload.user_id, payload.session_id or '',
                                  payload.message, language)
        outcome = app.idempotency.begin(payload.user_id, idem_key, hash_value)
        if outcome.status == 'conflict':
            raise HTTPException(409, 'Idempotency-Key was already used for a different request body')
        if outcome.status == 'in_progress':
            raise HTTPException(409, 'A request with this Idempotency-Key is already being processed')
        if outcome.status == 'replay':
            return ConversationResponse.model_validate_json(outcome.response_json)

    try:
        result = app.conversation.handle(user_id=payload.user_id, message=payload.message,
                                         session_id=payload.session_id, language=language,
                                         request_id=rid)
    except PermissionError as exc:
        # A session id belonging to a different user.
        if idem_key:
            app.idempotency.abandon(payload.user_id, idem_key)
        raise HTTPException(403, 'This session does not belong to this user') from exc
    except Exception:
        if idem_key:
            app.idempotency.abandon(payload.user_id, idem_key)
        raise

    if idem_key:
        app.idempotency.complete(payload.user_id, idem_key, result.model_dump_json())
    return result


@router.post('/v1/conversation/welcome', response_model=WelcomeResponse)
def welcome(payload: WelcomeRequest,
           app: Application = Depends(application),
           rid: str = Depends(request_id),
           authorized: frozenset = Depends(authorized_user_ids)) -> WelcomeResponse:
    """Open the app, create or restore a session, and get a deterministic
    proactive greeting -- with no fake ASR turn, no LLM call, and no effect
    on the very next real message the user sends. See
    ConversationManager.welcome for the exact guarantees."""
    if payload.user_id not in authorized:
        raise HTTPException(403, 'user_id is not authorized for this API credential')
    ensure_patient_active(app, payload.user_id)
    if payload.language and not app.languages.is_known(payload.language):
        raise HTTPException(400, f'Unknown language: {payload.language!r}')

    try:
        outcome = app.conversation.welcome(user_id=payload.user_id,
                                           session_id=payload.session_id,
                                           language=payload.language)
    except PermissionError as exc:
        raise HTTPException(403, 'This session does not belong to this user') from exc

    job_id = None
    job_status = 'NOT_REQUESTED'
    audio_reason = 'TTS_NOT_REQUESTED'
    if payload.speak:
        # Reuses the exact same async TTS job pipeline the voice endpoint
        # uses (VoiceJobRepository + VoiceJobWorker) -- no parallel audio
        # path. The worker itself already refuses to synthesize a language
        # no configured TTS provider supports and marks the job failed with
        # a specific reason, so this endpoint does not duplicate that check.
        job_id = app.voice_jobs.create(user_id=payload.user_id, session_id=outcome.session_id,
                                       language=outcome.language, response_text=outcome.text)
        submitted = app.voice_job_worker.submit(job_id) if app.voice_job_worker else False
        job_status = 'QUEUED' if submitted else 'FAILED'
        audio_reason = 'TTS_PROCESSING' if submitted else 'QUEUE_OVERLOADED'

    return WelcomeResponse(
        request_id=rid, session_id=outcome.session_id, response_text=outcome.text,
        language=outcome.language, session_restored=outcome.restored,
        job_id=job_id, job_status=job_status, audio_id=None, audio_url=None,
        audio_available=False, audio_unavailable_reason=audio_reason, tts_provider=None)
