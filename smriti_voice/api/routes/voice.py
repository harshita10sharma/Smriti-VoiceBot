"""Voice endpoints: upload one utterance, get an answer and an audio reference.

Audio never travels inside the JSON body or the logs.  The response carries an
opaque ``audio_id``; the client fetches the bytes once from ``/v1/audio/{id}``.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool

from ...app import Application
from ...idempotency import payload_hash
from ...pipeline import VoicePipeline
from ...schemas import VoiceJobCancelResponse, VoiceJobStatusResponse, VoiceResponse
from ..dependencies import (
    application,
    authorized_user_ids,
    ensure_patient_active,
    idempotency_key,
    request_id,
    require_api_key,
)
from ..upload import read_wav_upload

router = APIRouter(tags=['voice'], dependencies=[Depends(require_api_key)])

@router.post('/v1/conversation/voice', response_model=VoiceResponse)
async def conversation_voice(
    audio_wav: UploadFile = File(...),
    user_id: str = Form(...),
    session_id: str | None = Form(default=None),
    language: str | None = Form(default=None),
    speak: bool = Form(default=True),
    app: Application = Depends(application),
    rid: str = Depends(request_id),
    authorized: frozenset = Depends(authorized_user_ids),
    idem_key: str | None = Depends(idempotency_key),
) -> VoiceResponse:
    """One voice turn: WAV in, ASR + conversation run synchronously and
    ``response_text`` returns immediately, but TTS is asynchronous --
    ``job_id``/``job_status`` are returned instead of audio. Poll
    ``GET /v1/voice/jobs/{job_id}`` until it is no longer
    queued/processing, then ``GET /v1/audio/{audio_id}`` once. Same
    ownership and optional ``Idempotency-Key`` behavior as
    ``POST /v1/conversation`` (idempotency is keyed on the exact audio
    bytes plus ``user_id``/``session_id``/``language``/``speak``)."""
    if user_id not in authorized:
        raise HTTPException(403, 'user_id is not authorized for this API credential')
    ensure_patient_active(app, user_id)
    raw = await read_wav_upload(audio_wav, max_bytes=app.config.max_upload_bytes,
                                max_duration_s=app.config.max_wav_duration_s)

    if language:
        if not app.languages.is_known(language):
            raise HTTPException(400, f'Unknown language: {language!r}')
        # See the same normalization note in api/routes/conversation.py:
        # without this, a caller-supplied alias like 'hi' never matches the
        # canonical 'hin' key in the ASR-error text templates and silently
        # falls back to English.
        language = app.languages.get(language).code

    # Optional: same exactly-once guarantee as the text endpoint, keyed on
    # the raw audio bytes so a client retry of the same recording after a
    # timeout cannot run ASR/conversation/tool-execution a second time.
    if idem_key:
        hash_value = payload_hash(user_id, session_id or '', language or '', speak, raw)
        outcome = app.idempotency.begin(user_id, idem_key, hash_value)
        if outcome.status == 'conflict':
            raise HTTPException(409, 'Idempotency-Key was already used for a different request body')
        if outcome.status == 'in_progress':
            raise HTTPException(409, 'A request with this Idempotency-Key is already being processed')
        if outcome.status == 'replay':
            return VoiceResponse.model_validate_json(outcome.response_json)

    with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as handle:
        handle.write(raw)
        path = Path(handle.name)
    try:
        pipeline = VoicePipeline(app)
        result = await run_in_threadpool(pipeline.process, path, user_id=user_id,
                                         session_id=session_id, language=language,
                                         request_id=rid, speak=speak)
    except PermissionError as exc:
        # A session id belonging to a different user.
        if idem_key:
            app.idempotency.abandon(user_id, idem_key)
        raise HTTPException(403, 'This session does not belong to this user') from exc
    except Exception:
        if idem_key:
            app.idempotency.abandon(user_id, idem_key)
        raise
    finally:
        path.unlink(missing_ok=True)

    if idem_key:
        app.idempotency.complete(user_id, idem_key, result.model_dump_json())
    return result


@router.get('/v1/audio/{audio_id}')
def audio(audio_id: str, app: Application = Depends(application),
         authorized: frozenset = Depends(authorized_user_ids)) -> FileResponse:
    path = app.tts.store.path_for(audio_id)
    if path is None:
        raise HTTPException(404, 'Audio not found or expired')
    # Every audio_id in this system is produced by exactly one path (a voice
    # job's TTS synthesis), so this always resolves for legitimately-issued
    # ids. An id whose job belongs to a user this credential isn't
    # authorized for — or that has no owning job at all — is reported the
    # same as a missing file: fail closed, never serve unattributed audio.
    job = app.voice_jobs.get_by_audio_id(audio_id)
    if job is None or job.user_id not in authorized:
        raise HTTPException(404, 'Audio not found or expired')
    return FileResponse(path, media_type='audio/wav', filename=f'{audio_id}.wav')


@router.get('/v1/voice/jobs/{job_id}', response_model=VoiceJobStatusResponse)
def voice_job_status(
    job_id: str,
    app: Application = Depends(application),
    authorized: frozenset = Depends(authorized_user_ids),
) -> VoiceJobStatusResponse:
    job = app.voice_jobs.get(job_id)  # also lazily applies the processing deadline
    # A job belonging to a user this credential isn't authorized for is
    # reported the same as a missing one, so a job id cannot be used to
    # probe for other users' job ids.
    if job is None or job.user_id not in authorized:
        raise HTTPException(404, 'Job not found')
    audio_expired = (job.status == 'completed' and job.audio_id is not None
                     and app.tts.store.path_for(job.audio_id) is None)
    return VoiceJobStatusResponse(
        job_id=job.job_id, status=job.status, language=job.language,
        audio_id=job.audio_id,
        audio_url=f'/v1/audio/{job.audio_id}' if job.audio_id else None,
        tts_provider=job.tts_provider, error_code=job.error_code,
        audio_expired=audio_expired)


@router.post('/v1/voice/jobs/{job_id}/cancel', response_model=VoiceJobCancelResponse)
def cancel_voice_job(
    job_id: str,
    app: Application = Depends(application),
    authorized: frozenset = Depends(authorized_user_ids),
) -> VoiceJobCancelResponse:
    """Cancels a job still `queued` or `processing`. Same not-found-vs-
    unauthorized fail-closed behavior as job status/audio retrieval.
    Cancelling an already-terminal job (completed/failed/cancelled) is not
    an error -- it returns `cancelled=false` with that job's actual
    current status, since there is nothing left to cancel. See
    VoiceJobRepository.cancel for the exact race semantics against a
    worker that may be mid-synthesis at the same moment."""
    job = app.voice_jobs.get(job_id)
    if job is None or job.user_id not in authorized:
        raise HTTPException(404, 'Job not found')
    cancelled = app.voice_jobs.cancel(job_id)
    current = app.voice_jobs.get(job_id)
    return VoiceJobCancelResponse(job_id=job_id, status=current.status if current else job.status,
                                  cancelled=cancelled)
