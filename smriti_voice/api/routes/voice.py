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
from ...pipeline import VoicePipeline
from ...schemas import VoiceJobStatusResponse, VoiceResponse
from ..dependencies import application, authenticated_user_id, request_id, require_api_key
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
    authenticated_id: str = Depends(authenticated_user_id),
) -> VoiceResponse:
    if user_id != authenticated_id:
        raise HTTPException(403, 'user_id does not match the authenticated user')
    raw = await read_wav_upload(audio_wav, max_bytes=app.config.max_upload_bytes)

    if language and not app.languages.is_known(language):
        raise HTTPException(400, f'Unknown language: {language!r}')

    with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as handle:
        handle.write(raw)
        path = Path(handle.name)
    try:
        pipeline = VoicePipeline(app)
        return await run_in_threadpool(pipeline.process, path, user_id=user_id,
                                       session_id=session_id, language=language,
                                       request_id=rid, speak=speak)
    finally:
        path.unlink(missing_ok=True)


@router.get('/v1/audio/{audio_id}')
def audio(audio_id: str, app: Application = Depends(application)) -> FileResponse:
    path = app.tts.store.path_for(audio_id)
    if path is None:
        raise HTTPException(404, 'Audio not found or expired')
    return FileResponse(path, media_type='audio/wav', filename=f'{audio_id}.wav')


@router.get('/v1/voice/jobs/{job_id}', response_model=VoiceJobStatusResponse)
def voice_job_status(
    job_id: str,
    app: Application = Depends(application),
    authenticated_id: str = Depends(authenticated_user_id),
) -> VoiceJobStatusResponse:
    job = app.voice_jobs.get(job_id)
    # A job belonging to a different user is reported the same as a missing
    # one, so a job id cannot be used to probe for other users' job ids.
    if job is None or job.user_id != authenticated_id:
        raise HTTPException(404, 'Job not found')
    return VoiceJobStatusResponse(
        job_id=job.job_id, status=job.status, language=job.language,
        audio_id=job.audio_id,
        audio_url=f'/v1/audio/{job.audio_id}' if job.audio_id else None,
        tts_provider=job.tts_provider, error_code=job.error_code)
