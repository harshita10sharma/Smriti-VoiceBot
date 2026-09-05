"""Capability health, reported by ``GET /v1/health``.

Every value here is a measured boolean or a count.  No API key, no secret and no
personal data is ever returned.
"""
from __future__ import annotations

import os
from typing import Any

from ..config import AppConfig
from ..language.registry import LanguageService
from ..llm.router import LLMRouter
from ..schemas import LanguageStatus
from ..tts.router import TTSRouter
from .manager import OfflineManager


def build_health(*, config: AppConfig, languages: LanguageService, llm: LLMRouter,
                 tts: TTSRouter, offline: OfflineManager,
                 tool_count: int) -> dict[str, Any]:
    llm_providers = llm.available()
    tts_providers = tts.available()
    summary = languages.summary()
    connectivity = offline.snapshot()

    return {
        'status': 'ok',
        'version': config.version,
        'connectivity': connectivity,
        'providers': {
            # Booleans only. Never the keys themselves.
            'credentials_configured': config.providers.configured_providers(),
            'llm': llm_providers,
            'tts': tts_providers,
        },
        'capabilities': {
            'deterministic_commands': True,
            'personal_memory': True,
            'general_conversation': any(llm_providers.get(name) for name in
                                        ('gemini', 'openai', 'sarvam', 'local')),
            'offline_general_conversation': llm_providers.get('local', False),
            # True only if a provider is configured AND some language can be spoken.
            'voice_output': (any(tts_providers.get(name) for name in ('sarvam', 'local', 'indic_parler'))
                             and any(capability.tts_online for capability in languages.all())),
            'offline_voice_output': tts_providers.get('local', False),
        },
        'languages': {
            'configured': len(languages.all()),
            'by_status': {key: value for key, value in summary.items() if value},
            'validated': sum(1 for capability in languages.all() if capability.validated),
            'speakable': sum(1 for capability in languages.all() if capability.tts_online),
        },
        'tools': {'registered': tool_count},
        'configuration': {
            'offline_forced': config.offline_forced,
            'authentication_configured': bool(os.getenv(config.api_key_env)) and
            bool(os.getenv('SMRITI_AUTH_USER_ID')),
            'unauthenticated_access_allowed': config.allow_unauthenticated,
            'max_upload_bytes': config.max_upload_bytes,
            'max_history_turns': config.max_history_turns,
        },
    }
