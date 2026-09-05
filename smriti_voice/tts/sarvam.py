"""Sarvam Bulbul text-to-speech.

``POST https://api.sarvam.ai/text-to-speech`` with ``api-subscription-key``.
The response is JSON carrying an ``audios`` array of base64 WAV strings, not raw
binary.

Bulbul's language list is **shorter than Sarvam's ASR list**.  It documents ten
Indian languages plus English; Assamese, Bodo, Manipuri, Nepali and the
Northeast languages are not among them.  ``supports()`` reflects that honestly,
and :class:`~smriti_voice.tts.router.TTSRouter` turns an unsupported language
into an explicit "no voice output" result rather than speaking the wrong
language.
"""
from __future__ import annotations

import base64
import time
from typing import Any

import httpx

from ..exceptions import ProviderNotConfigured, ProviderTimeout, TTSError
from ..language.capabilities import SARVAM_TTS_LANGUAGES, SARVAM_TTS_VOICES
from ..logging import redact
from ..schemas import TTSResult

BASE_URL = 'https://api.sarvam.ai/text-to-speech'
MAX_TEXT_CHARS = 1500


class SarvamTTSProvider:
    name = 'sarvam'
    online = True

    def __init__(self, api_key: str, model: str = 'bulbul:v2', *, timeout: float = 30.0,
                 max_retries: int = 2, backoff: float = 0.5) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff = backoff

    def supports(self, language: str) -> bool:
        return language in SARVAM_TTS_LANGUAGES

    def synthesize(self, text: str, language: str, *, voice: str | None = None) -> TTSResult:
        if not self.api_key:
            raise ProviderNotConfigured('SARVAM_API_KEY is not configured')
        target = SARVAM_TTS_LANGUAGES.get(language)
        if not target:
            raise TTSError(f'Sarvam Bulbul does not support {language!r}')
        if not (text or '').strip():
            raise TTSError('Nothing to speak')

        started = time.perf_counter()
        body: dict[str, Any] = {
            'text': text[:MAX_TEXT_CHARS],
            'target_language_code': target,
            'model': self.model,
            'speaker': voice or SARVAM_TTS_VOICES.get(language, 'anushka'),
            # Slightly slower and a little louder: this is read by an elderly user.
            'pace': 0.9,
        }
        payload = self._post(body)
        audios = payload.get('audios') or []
        if not audios:
            raise TTSError('Sarvam returned no audio')
        try:
            audio = base64.b64decode(audios[0])
        except (ValueError, TypeError) as exc:
            raise TTSError('Sarvam returned audio that is not valid base64') from exc

        return TTSResult(audio=audio, language=language, voice=body['speaker'],
                         provider=self.name, model=self.model, mime_type='audio/wav',
                         latency_ms=int((time.perf_counter() - started) * 1000))

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        attempt = 0
        last: Exception | None = None
        headers = {'api-subscription-key': self.api_key, 'Content-Type': 'application/json'}
        while attempt <= self.max_retries:
            try:
                response = httpx.post(BASE_URL, headers=headers, json=body, timeout=self.timeout)
            except httpx.TimeoutException:
                last = ProviderTimeout(f'Sarvam TTS timed out after {self.timeout}s')
            except httpx.HTTPError as exc:
                last = TTSError(f'Sarvam TTS transport error: {type(exc).__name__}')
            else:
                if response.status_code < 400:
                    return response.json()
                if response.status_code in (429,) or response.status_code >= 500:
                    last = TTSError(f'Sarvam TTS HTTP {response.status_code}')
                else:
                    raise TTSError(f'Sarvam TTS HTTP {response.status_code}: '
                                   f'{str(redact(response.text))[:200]}')
            attempt += 1
            if attempt <= self.max_retries:
                time.sleep(self.backoff * (2 ** (attempt - 1)))
        raise last or TTSError('Sarvam TTS failed')
