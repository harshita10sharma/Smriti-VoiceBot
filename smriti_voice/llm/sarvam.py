"""Sarvam chat provider.

``POST https://api.sarvam.ai/v1/chat/completions`` is OpenAI-compatible and
authenticates with the ``api-subscription-key`` header.  Sarvam's Indic language
quality is the reason to use it; its tool-calling behaviour has **not** been
verified here, so ``supports_tools`` is False and the router will not send this
provider a tool-bearing turn.  Set ``SARVAM_TOOLS_VERIFIED=1`` after you have
tested it against your account to opt in.
"""
from __future__ import annotations

import os
import time
from typing import Any

from ..exceptions import ProviderNotConfigured
from ..schemas import LLMResponse
from .base import HTTPLLMProvider, Message, ToolSpec, elapsed_ms
from .openai import parse_openai_response, to_openai_messages

BASE_URL = 'https://api.sarvam.ai/v1/chat/completions'


class SarvamLLMProvider(HTTPLLMProvider):
    name = 'sarvam'
    online = True

    def __init__(self, api_key: str, model: str = 'sarvam-105b', **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.model = model
        self.supports_tools = (os.getenv('SARVAM_TOOLS_VERIFIED', '0') == '1')

    def generate(self, messages: list[Message], *, system: str = '',
                 tools: list[ToolSpec] | None = None,
                 temperature: float = 0.2, max_tokens: int = 512) -> LLMResponse:
        if not self.api_key:
            raise ProviderNotConfigured('SARVAM_API_KEY is not configured')
        started = time.perf_counter()
        body: dict[str, Any] = {
            'model': self.model,
            'messages': to_openai_messages(messages, system),
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if tools and self.supports_tools:
            body['tools'] = [{'type': 'function', 'function': {
                'name': t.name, 'description': t.description, 'parameters': t.parameters,
            }} for t in tools]
        payload = self._post(BASE_URL,
                             headers={'api-subscription-key': self.api_key,
                                      'Content-Type': 'application/json'},
                             json=body)
        return parse_openai_response(payload, self.name, self.model, elapsed_ms(started))
