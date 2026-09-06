"""Google Gemini provider (generateContent REST, v1beta).

Endpoint and payload shape follow the current Gemini API reference:
``POST https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent``
with ``tools[].functionDeclarations`` for tool calling.
Model name is environment-driven (``GEMINI_MODEL``) so it can be moved forward
without a code change.
"""
from __future__ import annotations

import time
from typing import Any

from ..exceptions import ProviderNotConfigured
from ..schemas import LLMResponse, ToolCall
from .base import HTTPLLMProvider, Message, ToolSpec, elapsed_ms

BASE_URL = 'https://generativelanguage.googleapis.com/v1beta/models'


class GeminiLLMProvider(HTTPLLMProvider):
    name = 'gemini'
    online = True
    supports_tools = True

    def __init__(self, api_key: str, model: str = 'gemini-3.6-flash', **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.api_key = api_key
        self.model = model

    def generate(self, messages: list[Message], *, system: str = '',
                 tools: list[ToolSpec] | None = None,
                 temperature: float = 0.2, max_tokens: int = 512) -> LLMResponse:
        if not self.api_key:
            raise ProviderNotConfigured('GEMINI_API_KEY is not configured')
        started = time.perf_counter()

        body: dict[str, Any] = {
            'contents': _to_contents(messages),
            'generationConfig': {'temperature': temperature, 'maxOutputTokens': max_tokens},
        }
        if system:
            body['systemInstruction'] = {'parts': [{'text': system}]}
        if tools:
            body['tools'] = [{'functionDeclarations': [
                {'name': t.name, 'description': t.description,
                 'parameters': _gemini_schema(t.parameters)}
                for t in tools
            ]}]

        payload = self._post(
            f'{BASE_URL}/{self.model}:generateContent',
            headers={'x-goog-api-key': self.api_key, 'Content-Type': 'application/json'},
            json=body,
        )
        return _parse(payload, self.model, elapsed_ms(started))


# Keys the shared Tool.json_schema() (standard Pydantic JSON Schema) includes
# that Gemini's function-declaration Schema rejects outright — e.g.
# `additionalProperties` gets "Unknown name \"additionalProperties\" ...
# Cannot find field" (HTTP 400), which otherwise looks like the whole
# provider is unavailable. OpenAI's and Sarvam's tool-calling accept this
# same shared schema unmodified, so the fix is scoped to Gemini only.
_GEMINI_UNSUPPORTED_SCHEMA_KEYS = {'additionalProperties'}


def _gemini_schema(schema: Any) -> Any:
    """Strip JSON Schema keys Gemini's parameter validator does not accept.

    Recurses into ``properties``/``items``/``$defs`` etc. so a nested object
    or array parameter is sanitised the same way as a top-level one.
    """
    if isinstance(schema, dict):
        return {key: _gemini_schema(value) for key, value in schema.items()
                if key not in _GEMINI_UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(schema, list):
        return [_gemini_schema(item) for item in schema]
    return schema


def _to_contents(messages: list[Message]) -> list[dict[str, Any]]:
    """Map neutral messages onto Gemini's ``contents`` array."""
    contents: list[dict[str, Any]] = []
    for message in messages:
        if message.role == 'system':
            continue  # carried in systemInstruction
        if message.role == 'tool':
            contents.append({'role': 'user', 'parts': [{'functionResponse': {
                'name': message.tool_name or 'tool',
                'response': {'result': message.content},
            }}]})
            continue
        if message.role == 'assistant' and message.tool_calls:
            parts = []
            for call in message.tool_calls:
                part: dict[str, Any] = {'functionCall': {
                    'name': call.get('name', ''), 'args': call.get('arguments', {})}}
                # Gemini 3.x requires this to be replayed verbatim on the next
                # turn, or it rejects the request with "Function call is
                # missing a thought_signature in functionCall parts".
                signature = call.get('thought_signature')
                if signature:
                    part['thoughtSignature'] = signature
                parts.append(part)
            contents.append({'role': 'model', 'parts': parts})
            continue
        contents.append({
            'role': 'model' if message.role == 'assistant' else 'user',
            'parts': [{'text': message.content or ''}],
        })
    return contents


def _parse(payload: dict[str, Any], model: str, latency_ms: int) -> LLMResponse:
    candidates = payload.get('candidates') or []
    text_parts: list[str] = []
    tool_calls: list[ToolCall] = []
    finish_reason = None
    if candidates:
        finish_reason = candidates[0].get('finishReason')
        for part in (candidates[0].get('content') or {}).get('parts') or []:
            if 'text' in part:
                text_parts.append(str(part['text']))
            call = part.get('functionCall')
            if call:
                args = call.get('args')
                tool_calls.append(ToolCall(name=str(call.get('name', '')),
                                           arguments=args if isinstance(args, dict) else {},
                                           thought_signature=part.get('thoughtSignature')))
    return LLMResponse(text=''.join(text_parts).strip(), tool_calls=tool_calls,
                       provider='gemini', model=model, latency_ms=latency_ms,
                       finish_reason=finish_reason)
