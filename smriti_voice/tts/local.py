"""Local (offline) text-to-speech.

No local TTS model ships with SMRITI.  Two honest options are wired up:

* ``SMRITI_LOCAL_TTS_URL`` — an HTTP endpoint for a locally hosted TTS server
  (Piper, Coqui, IndicTTS).  It must return WAV bytes.
* Nothing configured — the provider raises ``ProviderNotConfigured`` and the
  router reports "voice output unavailable" instead of inventing audio.

``supports()`` returns False for every language until the operator declares the
languages their local voice actually covers, via ``SMRITI_LOCAL_TTS_LANGUAGES``
(a comma-separated list of internal codes).
"""
from __future__ import annotations

import os
import time

import httpx

from ..exceptions import ProviderNotConfigured, TTSError
from ..schemas import TTSResult


class LocalTTSProvider:
    name = 'local'
    online = False

    def __init__(self, base_url: str | None = None, languages: str | None = None,
                 *, timeout: float = 20.0) -> None:
        self.base_url = (base_url or os.getenv('SMRITI_LOCAL_TTS_URL') or '').strip()
        declared = (languages if languages is not None else os.getenv('SMRITI_LOCAL_TTS_LANGUAGES', ''))
        self.languages = frozenset(code.strip() for code in declared.split(',') if code.strip())
        self.timeout = timeout

    def supports(self, language: str) -> bool:
        return bool(self.base_url) and language in self.languages

    def synthesize(self, text: str, language: str, *, voice: str | None = None) -> TTSResult:
        if not self.base_url:
            raise ProviderNotConfigured(
                'SMRITI_LOCAL_TTS_URL is not set. No offline voice is bundled with SMRITI.')
        if not self.supports(language):
            raise TTSError(f'Local TTS is not declared to support {language!r}')
        started = time.perf_counter()
        try:
            response = httpx.post(self.base_url, json={'text': text, 'language': language,
                                                       'voice': voice}, timeout=self.timeout)
        except httpx.HTTPError as exc:
            raise TTSError(f'Local TTS transport error: {type(exc).__name__}') from exc
        if response.status_code >= 400:
            raise TTSError(f'Local TTS HTTP {response.status_code}')
        return TTSResult(audio=response.content, language=language, voice=voice,
                         provider=self.name, offline=True, mime_type='audio/wav',
                         latency_ms=int((time.perf_counter() - started) * 1000))

    @property
    def is_reachable(self) -> bool:
        if not self.base_url:
            return False
        try:
            return httpx.get(self.base_url, timeout=2.0).status_code < 500
        except Exception:
            return False
