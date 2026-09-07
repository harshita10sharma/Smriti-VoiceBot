"""Groq chat provider."""
from __future__ import annotations

import os
import time
from typing import Any

from groq import Groq

from ..exceptions import LLMError, ProviderNotConfigured
from ..schemas import LLMResponse
from .base import HTTPLLMProvider, Message, ToolSpec, elapsed_ms
from .openai import parse_openai_response, to_openai_messages


class GroqLLMProvider(HTTPLLMProvider):
    name = 'groq'
    online = True
    supports_tools = True

    def __init__(self, api_key: str, model: str = 'qwen/qwen3.8-27b', **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.model = model
        self.client = Groq(api_key=api_key, timeout=self.timeout,
                            max_retries=self.max_retries)

    def generate(
        self,
        messages: list[Message],
        *,
        system: str = '',
        tools: list[ToolSpec] | None = None,
        temperature: float = 0.2,
        max_tokens: int = 512,
    ) -> LLMResponse:
        if not self.api_key:
            raise ProviderNotConfigured('GROQ_API_KEY is not configured')

        started = time.perf_counter()

        body: dict[str, Any] = {
            'model': self.model,
            'messages': to_openai_messages(messages, system),
            'temperature': temperature,
            'max_tokens': max_tokens,
        }

        if tools and self.supports_tools:
            body['tools'] = [
                {
                    'type': 'function',
                    'function': {
                        'name': t.name,
                        'description': t.description,
                        'parameters': t.parameters,
                    },
                }
                for t in tools
            ]

        try:
            response = self.client.chat.completions.create(**body)
        except Exception as exc:
            raise LLMError(f'{self.name} request failed: {type(exc).__name__}') from exc

        payload = response.model_dump()
        return parse_openai_response(
            payload,
            self.name,
            self.model,
            elapsed_ms(started),
        )
