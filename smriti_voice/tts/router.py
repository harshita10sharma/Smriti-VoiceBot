"""Chooses a voice for a response, and refuses to speak the wrong language.

The rule from the specification is implemented literally: if no configured
provider supports the response language, the router does **not** substitute
another language.  It returns a ``TTSResult`` with ``available=False`` and a
reason, and the API surfaces that to the UI so it can show text instead.
"""
from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

from ..config import AppConfig
from ..exceptions import ProviderNotConfigured, TTSError
from ..language.registry import LanguageService
from ..logging import get_logger
from ..schemas import TTSResult
from .local import LocalTTSProvider
from .mock import MockTTSProvider
from .sarvam import SarvamTTSProvider
from .indic_parler import IndicParlerTTSProvider

log = get_logger('tts.router')


class AudioStore:
    """Short-lived on-disk cache so audio never travels through logs or JSON.

    The API hands out an opaque id; the client fetches the bytes once from
    ``/v1/audio/{id}``.  Files older than the retention window are deleted on
    every write, which keeps the directory bounded without a background task.
    """

    def __init__(self, directory: Path, retention_minutes: int = 15) -> None:
        self.directory = Path(directory)
        self.retention_s = max(60, retention_minutes * 60)

    def put(self, audio: bytes, *, suffix: str = '.wav') -> str:
        self.directory.mkdir(parents=True, exist_ok=True)
        self.prune()
        digest = hashlib.sha256(audio + str(time.time_ns()).encode()).hexdigest()[:32]
        path = self.directory / f'{digest}{suffix}'
        path.write_bytes(audio)
        return digest

    def path_for(self, audio_id: str) -> Path | None:
        # The id is attacker-supplied: allow hex only, and never join a path.
        if not audio_id or not all(ch in '0123456789abcdef' for ch in audio_id):
            return None
        path = self.directory / f'{audio_id}.wav'
        return path if path.is_file() else None

    def prune(self) -> int:
        if not self.directory.exists():
            return 0
        cutoff = time.time() - self.retention_s
        removed = 0
        for path in self.directory.glob('*.wav'):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed


class TTSRouter:
    def __init__(self, config: AppConfig, languages: LanguageService,
                 store: AudioStore | None = None) -> None:
        self.config = config
        self.languages = languages
        self.store = store or AudioStore(config.audio_cache_dir, config.audio_retention_minutes)
        self._cache: dict[str, object] = {}

    # ------------------------------------------------------------------ #
    def build(self, name: str):
        if name in self._cache:
            return self._cache[name]
        providers = self.config.providers
        if name == 'sarvam':
            if not providers.sarvam_key:
                raise ProviderNotConfigured('SARVAM_API_KEY is not configured')
            provider = SarvamTTSProvider(providers.sarvam_key, providers.tts_model,
                                         timeout=providers.tts_timeout_s,
                                         max_retries=providers.max_retries,
                                         backoff=providers.retry_backoff_s)
        elif name == 'local':
            provider = LocalTTSProvider()
            # An unconfigured local provider must not count as an available voice:
            # /v1/health would then claim voice output that cannot be produced.
            if not provider.base_url or not provider.languages:
                raise ProviderNotConfigured(
                    'SMRITI_LOCAL_TTS_URL and SMRITI_LOCAL_TTS_LANGUAGES are not both set')
        elif name == 'mock':
            provider = MockTTSProvider()
        elif name == 'indic_parler':
            if not providers.indic_parler_enabled:
                raise ProviderNotConfigured('SMRITI_INDIC_PARLER_ENABLED is not set to true')
            provider = IndicParlerTTSProvider(
                model_name=providers.indic_parler_model,
                device=providers.indic_parler_device,
                languages=providers.indic_parler_languages,
                description=providers.indic_parler_description,
                timeout=providers.tts_timeout_s
            )
        else:
            raise ProviderNotConfigured(f'Unknown TTS provider {name!r}')
        self._cache[name] = provider
        return provider

    def candidates(self) -> list[str]:
        configured = (self.config.providers.tts_provider or 'auto').lower()
        if configured != 'auto':
            return [configured]
        if self.config.offline_forced:
            providers = ['local']
            if self.config.providers.indic_parler_enabled:
                providers.append('indic_parler')
            return providers
        providers = ['local', 'sarvam']
        if self.config.providers.indic_parler_enabled:
            providers.append('indic_parler')
        return providers

    def available(self) -> dict[str, bool]:
        status: dict[str, bool] = {}
        for name in ('sarvam', 'local', 'mock', 'indic_parler'):
            try:
                self.build(name)
                status[name] = True
            except Exception:
                status[name] = False
        return status

    # ------------------------------------------------------------------ #
    def synthesize(self, text: str, language: str, *, request_id: str = '',
                   store: bool = True) -> TTSResult:
        """Speak ``text`` in ``language``, or explain why we cannot."""
        if not (text or '').strip():
            return TTSResult(language=language, available=False,
                             unavailable_reason='EMPTY_RESPONSE_TEXT')

        tried: list[str] = []
        for name in self.candidates():
            try:
                provider = self.build(name)
            except Exception:
                tried.append(f'{name}:not_configured')
                continue
            if not provider.supports(language):
                tried.append(f'{name}:language_unsupported')
                continue
            try:
                result = provider.synthesize(text, language,
                                             voice=self.languages.tts_voice(language))
            except Exception as exc:
                log.warning('tts_failed', fields={'request_id': request_id, 'provider': name,
                                                  'language': language,
                                                  'error': type(exc).__name__})
                tried.append(f'{name}:{type(exc).__name__}')
                continue
            if store and result.audio:
                # `audio` is excluded from serialisation, not from the model, so
                # model_copy keeps the bytes while adding the fetch id.
                result = result.model_copy(update={'audio_id': self.store.put(result.audio)})
            return result

        # Distinguish "nothing can ever speak this language" from "the provider that
        # could speak it failed just now". Providers that are simply not configured
        # say nothing about the language, so they are excluded from that judgement.
        considered = [item for item in tried if not item.endswith(':not_configured')]
        reason = ('NO_TTS_PROVIDER_SUPPORTS_LANGUAGE'
                  if considered and all('language_unsupported' in item for item in considered)
                  else 'TTS_UNAVAILABLE')
        log.info('tts_unavailable', fields={'request_id': request_id, 'language': language,
                                            'tried': tried})
        return TTSResult(language=language, available=False, unavailable_reason=reason,
                         provider='none')
