"""The v4.1 command endpoint, preserved.

Same path, same multipart fields, same response body as VoiceBot v4.1, so an
existing Flutter or web client keeps working unchanged after the migration.  It
still runs the original :class:`~smriti_voice.engine.VoiceEngine`.
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from ...app import Application
from ...config import Settings
from ...engine import VoiceEngine
from ..dependencies import application, require_api_key
from ..upload import read_wav_upload

router = APIRouter(tags=['command'], dependencies=[Depends(require_api_key)])

_engine: VoiceEngine | None = None


def _get_engine() -> VoiceEngine:
    global _engine
    if _engine is None:
        _engine = VoiceEngine(Settings.load())
    return _engine


@router.post('/v1/command')
async def command(audio_wav: UploadFile = File(...), language: str = Form(...),
                  request_id: str | None = Form(default=None),
                  app: Application = Depends(application)) -> dict:
    raw = await read_wav_upload(audio_wav, max_bytes=app.config.max_upload_bytes,
                                max_duration_s=app.config.max_wav_duration_s)

    with tempfile.NamedTemporaryFile(delete=False, suffix='.wav') as handle:
        handle.write(raw)
        path = Path(handle.name)
    try:
        result = await run_in_threadpool(_get_engine().process, path, language, request_id)
        return result.__dict__
    finally:
        path.unlink(missing_ok=True)
