"""The voice pipeline: audio in, text back immediately, audio via a job.

    WAV → validate → ASR → language detection → conversation → [TTS job queued]

ASR and conversation run synchronously (both are fast). TTS does not: Indic
Parler-TTS can take well over a minute on CPU, past most HTTP proxy
timeouts, so speech synthesis is queued as a job and the response returns
immediately with a job id. The caller polls GET /v1/voice/jobs/{job_id}
until it is 'completed', then fetches GET /v1/audio/{audio_id}.

Each stage records its own latency and degrades on its own terms: a failed
ASR returns a structured error rather than an empty transcript that the
router would misread as silence.
"""
from __future__ import annotations

import io
import time
import uuid
import wave
from pathlib import Path

from .app import Application
from .exceptions import AudioError
from .logging import get_logger
from .schemas import ExecutionMode, TurnKind, VoiceResponse

log = get_logger('pipeline')

MIN_WAV_BYTES = 44


def validate_wav_bytes(raw: bytes, *, max_bytes: int, max_duration_s: float | None = None) -> None:
    """Reject anything that is not a plausible WAV before it reaches a decoder.

    Container/size checks are unconditional. The duration check is
    deliberately best-effort and fail-open: it uses the stdlib ``wave``
    module to compute the real duration from the parsed header, and only
    ever rejects on a *positively confirmed* excessive duration. A file
    that ``wave`` cannot parse (non-standard chunk ordering, a codec the
    lightweight header check above still accepted) is left to the existing
    RIFF/WAVE/data checks and, ultimately, the ASR provider -- this must
    never become a new source of false rejections for audio that would
    otherwise have worked.
    """
    if not raw:
        raise AudioError('Audio payload is empty')
    if len(raw) > max_bytes:
        raise AudioError(f'Audio file is larger than {max_bytes} bytes')
    if len(raw) < 12 or raw[:4] != b'RIFF' or raw[8:12] != b'WAVE':
        raise AudioError('Only valid WAV audio is accepted')
    if len(raw) < MIN_WAV_BYTES:
        raise AudioError('WAV file is truncated or malformed')
    if b'data' not in raw[12:]:
        raise AudioError('WAV file is missing audio data')

    if max_duration_s is not None:
        try:
            with wave.open(io.BytesIO(raw), 'rb') as handle:
                rate = handle.getframerate()
                frames = handle.getnframes()
                duration_s = frames / rate if rate else 0.0
        except Exception:
            return  # fail-open: an unparseable-by-wave file is not rejected here
        if duration_s > max_duration_s:
            raise AudioError(f'Audio duration ({duration_s:.1f}s) exceeds the '
                             f'{max_duration_s:.0f}s limit')


class VoicePipeline:
    def __init__(self, application: Application) -> None:
        self.app = application

    def process(self, wav_path: Path, *, user_id: str, session_id: str | None = None,
                language: str | None = None, request_id: str | None = None,
                speak: bool = True) -> VoiceResponse:
        rid = request_id or uuid.uuid4().hex
        started = time.perf_counter()

        # 1. Speech to text.
        try:
            asr = self.app.asr.transcribe(wav_path, language or 'auto', request_id=rid)
        except Exception as exc:
            log.warning('asr_failed', fields={'request_id': rid, 'error': type(exc).__name__})
            return self._error(rid, session_id, language or 'eng', 'ASR_UNAVAILABLE',
                               started)

        if not asr.transcript.strip():
            return self._error(rid, session_id, language or 'eng', 'NO_SPEECH_DETECTED',
                               started, asr_provider=asr.provider,
                               asr_latency_ms=asr.latency_ms)

        # 2. Which language was that?
        detection = self.app.detector.detect(asr.transcript, provider_hint=asr.language,
                                             provider_confidence=asr.language_confidence)
        turn_language = language or detection.language

        # 3. The conversation turn (safety, commands, tools, model).
        reply = self.app.conversation.handle(user_id=user_id, message=asr.transcript,
                                             session_id=session_id, language=turn_language,
                                             request_id=rid)

        # 4. Queue speech synthesis; the caller polls for the result rather
        # than waiting on this request for it.
        job_id = None
        job_status = 'NOT_REQUESTED'
        audio_reason = 'TTS_NOT_REQUESTED'
        if speak:
            job_id = self.app.voice_jobs.create(
                user_id=user_id, session_id=reply.session_id, language=turn_language,
                response_text=reply.response_text)
            self.app.voice_job_worker.submit(job_id)
            job_status = 'QUEUED'
            audio_reason = 'TTS_PROCESSING'

        metadata = reply.metadata.model_copy(update={
            'asr_provider': asr.provider, 'asr_latency_ms': asr.latency_ms,
            'offline': asr.offline,
            'total_latency_ms': int((time.perf_counter() - started) * 1000),
        })

        # `language_confidence` is re-derived from detection, so it must be excluded
        # from the conversation payload or it would be supplied twice.
        return VoiceResponse(
            **reply.model_dump(exclude={'metadata', 'language_confidence'}),
            metadata=metadata,
            transcript=asr.transcript,
            language_confidence=detection.confidence,
            job_id=job_id,
            job_status=job_status,
            audio_id=None,
            audio_url=None,
            audio_available=False,
            audio_unavailable_reason=audio_reason,
            tts_provider=None)

    def _error(self, request_id: str, session_id: str | None, language: str, code: str,
               started: float, *, asr_provider: str | None = None,
               asr_latency_ms: int = 0) -> VoiceResponse:
        from .schemas import TurnMetadata
        texts = {
            'ASR_UNAVAILABLE': {
                'eng': 'I could not hear that. Please tap the microphone and try once more.',
                'hin': 'मैं सुन नहीं पाई। कृपया माइक दबाकर फिर से कहिए।',
                'asm': 'মই শুনিব পৰা নাই। অনুগ্ৰহ কৰি মাইক টিপি আকৌ কওক।',
                'ben': 'আমি শুনতে পাইনি। অনুগ্রহ করে মাইক চেপে আবার বলুন।'},
            'NO_SPEECH_DETECTED': {
                'eng': 'I did not hear anything. Please tap the microphone and speak again.',
                'hin': 'मुझे कुछ सुनाई नहीं दिया। कृपया माइक दबाकर फिर बोलिए।',
                'asm': 'মই একো শুনা নাই। অনুগ্ৰহ কৰি আকৌ কওক।',
                'ben': 'আমি কিছু শুনতে পাইনি। অনুগ্রহ করে আবার বলুন।'},
        }[code]
        return VoiceResponse(
            request_id=request_id, session_id=session_id or '', language=language,
            response_text=texts.get(language, texts['eng']), kind=TurnKind.ERROR,
            transcript='', audio_available=False, audio_unavailable_reason=code,
            metadata=TurnMetadata(request_id=request_id, session_id=session_id or '',
                                  kind=TurnKind.ERROR, execution_mode=ExecutionMode.ERROR,
                                  asr_provider=asr_provider, asr_latency_ms=asr_latency_ms,
                                  error_code=code,
                                  total_latency_ms=int((time.perf_counter() - started) * 1000)))
