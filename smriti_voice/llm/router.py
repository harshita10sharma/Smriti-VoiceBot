"""Picks an LLM provider for a turn, with a bounded fallback chain.

Selection order (when ``SMRITI_LLM_PROVIDER=auto``):

1. A local LLM, if one is configured and reachable — offline-first.
2. Gemini, then OpenAI: both verified tool-callers.
3. Sarvam, for its Indic strength, but only for turns that need no tools
   (its tool-calling is unverified here).

Anything explicitly named in ``SMRITI_LLM_PROVIDER`` wins and is not silently
replaced: if it fails, the failure is reported rather than papered over with a
different vendor's answer.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import AppConfig
from ..exceptions import LLMError, ProviderNotConfigured
from ..logging import get_logger
from ..schemas import LLMResponse
from .base import LLMProvider, Message, ToolSpec
from .gemini import GeminiLLMProvider
from .local import LocalLLMProvider
from .mock import MockLLMProvider
from .openai import OpenAILLMProvider
from .sarvam import SarvamLLMProvider

log = get_logger('llm.router')


@dataclass
class LLMAttempt:
    provider: str
    ok: bool
    error: str | None = None


class LLMRouter:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._cache: dict[str, LLMProvider] = {}
        self.last_attempts: list[LLMAttempt] = []

    # ------------------------------------------------------------------ #
    def build(self, name: str) -> LLMProvider:
        if name in self._cache:
            return self._cache[name]
        providers = self.config.providers
        common = {'timeout': providers.llm_timeout_s, 'max_retries': providers.max_retries,
                  'backoff': providers.retry_backoff_s}
        if name == 'gemini':
            if not providers.gemini_key:
                raise ProviderNotConfigured('GEMINI_API_KEY is not configured')
            provider: LLMProvider = GeminiLLMProvider(providers.gemini_key,
                                                      providers.llm_model_gemini, **common)
        elif name == 'openai':
            if not providers.openai_key:
                raise ProviderNotConfigured('OPENAI_API_KEY is not configured')
            provider = OpenAILLMProvider(providers.openai_key, providers.llm_model_openai, **common)
        elif name == 'sarvam':
            if not providers.sarvam_key:
                raise ProviderNotConfigured('SARVAM_API_KEY is not configured')
            provider = SarvamLLMProvider(providers.sarvam_key, providers.llm_model_sarvam, **common)
        elif name == 'local':
            provider = LocalLLMProvider(**common)
        elif name == 'mock':
            provider = MockLLMProvider()
        else:
            raise ProviderNotConfigured(f'Unknown LLM provider {name!r}')
        self._cache[name] = provider
        return provider

    # ------------------------------------------------------------------ #
    def candidates(self, *, needs_tools: bool) -> list[str]:
        configured = (self.config.providers.llm_provider or 'auto').lower()
        if configured != 'auto':
            return [configured]
        order = ['local', 'gemini', 'openai']
        if not needs_tools:
            order.append('sarvam')
        if self.config.offline_forced:
            return ['local']
        return order

    def available(self) -> dict[str, bool]:
        """Which providers could be built right now.  Booleans only."""
        status: dict[str, bool] = {}
        for name in ('local', 'gemini', 'openai', 'sarvam', 'mock'):
            try:
                self.build(name)
                status[name] = True
            except Exception:
                status[name] = False
        return status

    # ------------------------------------------------------------------ #
    def generate(self, messages: list[Message], *, system: str = '',
                 tools: list[ToolSpec] | None = None, temperature: float = 0.2,
                 max_tokens: int = 512, request_id: str = '') -> LLMResponse:
        needs_tools = bool(tools)
        self.last_attempts = []
        last_error: Exception | None = None

        for name in self.candidates(needs_tools=needs_tools):
            try:
                provider = self.build(name)
            except Exception as exc:
                self.last_attempts.append(LLMAttempt(name, False, type(exc).__name__))
                last_error = exc
                continue
            # Never hand tools to a provider whose tool support is unverified:
            # it would answer from imagination instead of from the database.
            usable_tools = tools if getattr(provider, 'supports_tools', False) else None
            if needs_tools and usable_tools is None:
                self.last_attempts.append(LLMAttempt(name, False, 'tools_unsupported'))
                continue
            try:
                response = provider.generate(messages, system=system, tools=usable_tools,
                                             temperature=temperature, max_tokens=max_tokens)
                self.last_attempts.append(LLMAttempt(name, True))
                return response
            except Exception as exc:
                log.warning('llm_provider_failed',
                            fields={'request_id': request_id, 'provider': name,
                                    'error': type(exc).__name__})
                self.last_attempts.append(LLMAttempt(name, False, type(exc).__name__))
                last_error = exc

        raise LLMError(f'No LLM provider could answer: '
                       f'{[a.provider + ":" + (a.error or "ok") for a in self.last_attempts]}') \
            from last_error

    @property
    def fallback_used(self) -> bool:
        """True when the answer did not come from the first-choice provider."""
        return len(self.last_attempts) > 1 and self.last_attempts[-1].ok
