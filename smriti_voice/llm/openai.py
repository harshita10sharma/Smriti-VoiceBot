"""OpenAI Chat Completions provider.

Uses ``POST https://api.openai.com/v1/chat/completions`` with the ``tools``
parameter.  (``smriti_voice.intent_semantic`` separately uses the Responses API
for constrained action classification; that v4.1 module is untouched.)
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..exceptions import ProviderNotConfigured
from ..schemas import LLMResponse, ToolCall
from .base import HTTPLLMProvider, Message, ToolSpec, elapsed_ms

BASE_URL = 'https://api.openai.com/v1/chat/completions'


class OpenAILLMProvider(HTTPLLMProvider):
    name = 'openai'
    online = True
    supports_tools = True

    def __init__(self, api_key: str, model: str = 'gpt-4o-mini', *,
                 base_url: str = BASE_URL, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.model = model
        self.base_url = base_url

    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'}

    def generate(self, messages: list[Message], *, system: str = '',
                 tools: list[ToolSpec] | None = None,
                 temperature: float = 0.2, max_tokens: int = 512) -> LLMResponse:
        if not self.api_key:
            raise ProviderNotConfigured(f'{self.name} API key is not configured')
        started = time.perf_counter()

        body: dict[str, Any] = {
            'model': self.model,
            'messages': to_openai_messages(messages, system),
            'temperature': temperature,
            'max_tokens': max_tokens,
        }
        if tools:
            body['tools'] = [{'type': 'function', 'function': {
                'name': t.name, 'description': t.description, 'parameters': t.parameters,
            }} for t in tools]
            body['tool_choice'] = 'auto'

        payload = self._post(self.base_url, headers=self._headers(), json=body)
        return parse_openai_response(payload, self.name, self.model, elapsed_ms(started),
                                     offline=not self.online)


def to_openai_messages(messages: list[Message], system: str = '') -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if system:
        out.append({'role': 'system', 'content': system})
    for message in messages:
        if message.role == 'tool':
            out.append({'role': 'tool', 'content': message.content,
                        'tool_call_id': message.tool_call_id or message.tool_name or 'tool'})
            continue
        entry: dict[str, Any] = {'role': message.role, 'content': message.content or ''}
        if message.role == 'assistant' and message.tool_calls:
            entry['tool_calls'] = [{
                'id': call.get('call_id') or f'call_{index}',
                'type': 'function',
                'function': {'name': call.get('name', ''),
                             'arguments': json.dumps(call.get('arguments', {}), ensure_ascii=False)},
            } for index, call in enumerate(message.tool_calls)]
        out.append(entry)
    return out


def parse_openai_response(payload: dict[str, Any], provider: str, model: str,
                          latency_ms: int, *, offline: bool = False) -> LLMResponse:
    choices = payload.get('choices') or []
    text = ''
    tool_calls: list[ToolCall] = []
    finish_reason = None
    if choices:
        message = choices[0].get('message') or {}
        finish_reason = choices[0].get('finish_reason')
        text = str(message.get('content') or '').strip()
        for call in message.get('tool_calls') or []:
            function = call.get('function') or {}
            raw_args = function.get('arguments')
            try:
                arguments = json.loads(raw_args) if isinstance(raw_args, str) else (raw_args or {})
            except json.JSONDecodeError:
                # A model that emits invalid JSON gets an empty argument set; the
                # tool's schema validation then rejects it explicitly.
                arguments = {}
            if not isinstance(arguments, dict):
                arguments = {}
            tool_calls.append(ToolCall(name=str(function.get('name', '')), arguments=arguments,
                                       call_id=call.get('id')))
    return LLMResponse(text=text, tool_calls=tool_calls, provider=provider, model=model,
                       latency_ms=latency_ms, offline=offline, finish_reason=finish_reason)
