"""Chooses an ASR provider for a turn and records what actually happened.

Policy (unchanged from v4.1 in spirit, now explicit and testable):

1. A known language with a provisioned local model → local first, no network.
2. Otherwise, or if the local model fails → Sarvam (Indic + English), then OpenAI.
3. Forced-offline mode never touches the network, and says so.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

from ..config import AppConfig, LanguageRegistry
from ..exceptions import ProviderNotConfigured
from ..logging import get_logger
from ..schemas import ASRResult
from .providers import ASRError, OpenAIASR, ProviderFactory, SarvamASR

log = get_logger('asr.router')

ASRTurn = ASRResult  # readable alias at call sites


class ASRRouter:
    def __init__(self, config: AppConfig, registry: LanguageRegistry | None = None) -> None:
        self.config = config
        self.registry = registry or LanguageRegistry(config.settings.language_pack_dir)
        self.factory = ProviderFactory(Path(config.settings.language_pack_dir).parent)
        self._local_cache: dict[str, object] = {}

    # ------------------------------------------------------------------ #
    def local_provider(self, language: str):
        """Provisioned local provider, or raise.  Never downloads at runtime."""
        if language in self._local_cache:
            return self._local_cache[language]
        pack = self.registry.get(language)
        if pack.status != 'validated_local':
            raise ASRError(f'Local ASR is not production-enabled for {language} ({pack.status})')
        last: Exception | None = None
        for provider in pack.providers:
            if provider in {'sarvam', 'openai'}:
                continue
            try:
                obj = self.factory.create(provider, language, pack.model_id)
                obj.warmup()
                self._local_cache[language] = obj
                return obj
            except Exception as exc:  # provider missing on this device
                last = exc
        raise ASRError(f'No local provider available for {language}: {last}')

    def online_provider(self, language: str, auto: bool = False):
        if self.config.offline_forced:
            raise ProviderNotConfigured('offline mode is forced; no online ASR')
        if auto or language in SarvamASR.SUPPORTED:
            key = self.config.providers.sarvam_key
            if key:
                return SarvamASR(key, os.getenv('SARVAM_STT_MODEL', 'saaras:v4'),
                                 timeout=self.config.providers.request_timeout_s)
        key = self.config.providers.openai_key
        if key:
            return OpenAIASR(key, os.getenv('OPENAI_STT_MODEL', 'gpt-4o-transcribe'),
                             timeout=self.config.providers.request_timeout_s)
        raise ProviderNotConfigured('No online ASR provider is configured')

    # ------------------------------------------------------------------ #
    def transcribe(self, wav: Path, language: str, *, request_id: str = '') -> ASRResult:
        """Transcribe one utterance, preferring local, falling back online."""
        started = time.perf_counter()
        errors: list[str] = []

        if language and language != 'auto':
            try:
                self.registry.get(language)
                known = True
            except KeyError:
                known = False
            if known and not self.config.offline_forced or (known and self.config.offline_forced):
                try:
                    provider = self.local_provider(language)
                    transcript, detected, confidence = provider.transcribe(wav, language)
                    return ASRResult(transcript=transcript, language=detected or language,
                                     language_confidence=confidence, provider=provider.name,
                                     offline=True, latency_ms=_ms(started))
                except Exception as exc:
                    errors.append(f'local:{type(exc).__name__}')

        try:
            provider = self.online_provider(language, auto=(language in ('', 'auto')))
            transcript, detected, confidence = provider.transcribe(wav, language or 'auto')
            return ASRResult(transcript=transcript, language=detected, language_confidence=confidence,
                             provider=provider.name, offline=False, latency_ms=_ms(started))
        except Exception as exc:
            errors.append(f'online:{type(exc).__name__}')

        log.warning('asr_unavailable', fields={'request_id': request_id, 'language': language,
                                               'errors': errors})
        raise ASRError(f'ASR unavailable for {language!r}: {", ".join(errors)}')


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
