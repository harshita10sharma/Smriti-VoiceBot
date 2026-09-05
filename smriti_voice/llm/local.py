"""Local (offline) LLM provider.

SMRITI does not bundle model weights.  This provider talks to a locally running
OpenAI-compatible inference server — ``llama.cpp``'s ``llama-server``, Ollama, or
vLLM — at ``SMRITI_LOCAL_LLM_URL``.  That keeps the runtime free of a heavyweight
Python inference dependency and lets the device team pick the runtime.

If the URL is not configured the provider raises ``ProviderNotConfigured``; it
never pretends to answer.  See OFFLINE.md for the provisioning steps.
"""
from __future__ import annotations

import os
from typing import Any

from ..exceptions import ProviderNotConfigured
from .openai import OpenAILLMProvider


class LocalLLMProvider(OpenAILLMProvider):
    """OpenAI-compatible client pointed at localhost.  Marked offline."""

    name = 'local'
    online = False

    def __init__(self, base_url: str | None = None, model: str | None = None, **kwargs: Any) -> None:
        url = (base_url or os.getenv('SMRITI_LOCAL_LLM_URL') or '').strip()
        if not url:
            raise ProviderNotConfigured(
                'SMRITI_LOCAL_LLM_URL is not set. No local LLM is bundled with SMRITI; '
                'run llama.cpp/Ollama/vLLM locally and point this variable at it.')
        super().__init__(api_key=os.getenv('SMRITI_LOCAL_LLM_KEY', 'local'),
                         model=model or os.getenv('SMRITI_LOCAL_LLM_MODEL', 'local-model'),
                         base_url=url.rstrip('/') + ('' if url.rstrip('/').endswith('completions')
                                                     else '/v1/chat/completions'),
                         **kwargs)

    @property
    def is_reachable(self) -> bool:
        """Cheap liveness probe used by the offline health check."""
        import httpx
        try:
            root = self.base_url.split('/v1/')[0]
            return httpx.get(f'{root}/health', timeout=2.0).status_code < 500
        except Exception:
            return False
