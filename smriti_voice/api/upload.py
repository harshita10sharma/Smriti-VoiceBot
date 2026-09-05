"""Bounded multipart audio reads shared by the audio endpoints."""
from __future__ import annotations

from fastapi import HTTPException, UploadFile

from ..exceptions import AudioError
from ..pipeline import validate_wav_bytes

ACCEPTED_CONTENT_TYPES = {
    'audio/wav', 'audio/x-wav', 'audio/wave', 'application/octet-stream'
}
READ_CHUNK_BYTES = 1024 * 1024


async def read_wav_upload(upload: UploadFile, *, max_bytes: int) -> bytes:
    if upload.content_type not in ACCEPTED_CONTENT_TYPES:
        raise HTTPException(415, 'Only WAV audio is accepted')

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(READ_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(413, f'Audio file is larger than {max_bytes} bytes')
        chunks.append(chunk)

    raw = b''.join(chunks)
    try:
        validate_wav_bytes(raw, max_bytes=max_bytes)
    except AudioError as exc:
        message = str(exc)
        if 'empty' in message:
            raise HTTPException(400, message) from exc
        if 'larger than' in message:
            raise HTTPException(413, message) from exc
        raise HTTPException(415, message) from exc
    return raw