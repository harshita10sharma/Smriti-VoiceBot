"""LLM provider contract and the shared HTTP plumbing.

Every provider returns a :class:`~smriti_voice.schemas.LLMResponse`.  A provider
never executes anything: if the model wants a tool, the provider reports a
:class:`~smriti_voice.schemas.ToolCall` and stops.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

import httpx

from ..exceptions import LLMError, ProviderTimeout
from ..logging import get_logger
from ..schemas import LLMResponse

log = get_logger('llm')

Role = Literal['system', 'user', 'assistant', 'tool']


@dataclass
class Message:
    """One conversation message in provider-neutral form."""
    role: Role
    content: str = ''
    tool_call_id: str | None = None
    tool_name: str | None = None
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ToolSpec:
    """A tool as advertised to the model.  ``parameters`` is JSON Schema."""
    name: str
    description: str
    parameters: dict[str, Any]


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    online: bool
    supports_tools: bool

    def generate(self, messages: list[Message], *, system: str = '',
                 tools: list[ToolSpec] | None = None,
                 temperature: float = 0.2, max_tokens: int = 512) -> LLMResponse: ...


class HTTPLLMProvider:
    """Base for HTTP providers: bounded retry, timeouts, no secrets in errors."""

    name = 'http'
    online = True
    supports_tools = False

    def __init__(self, *, timeout: float = 30.0, max_retries: int = 2,
                 backoff: float = 0.5) -> None:
        self.timeout = timeout
        self.max_retries = max(0, max_retries)
        self.backoff = backoff

    def _post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> dict[str, Any]:
        """POST with bounded retry.  Retries transport errors, 429 and 5xx only —
        an auth failure or a bad request is never retried."""
        attempt = 0
        last_error: Exception | None = None
        while attempt <= self.max_retries:
            try:
                response = httpx.post(url, headers=headers, json=json, timeout=self.timeout)
            except httpx.TimeoutException as exc:
                last_error = ProviderTimeout(f'{self.name} timed out after {self.timeout}s')
            except httpx.HTTPError as exc:
                last_error = LLMError(f'{self.name} transport error: {type(exc).__name__}')
            else:
                if response.status_code < 400:
                    return response.json()
                if response.status_code in (408, 409, 425, 429) or response.status_code >= 500:
                    last_error = LLMError(f'{self.name} HTTP {response.status_code}')
                else:
                    # 4xx: the request or the key is wrong. Retrying cannot help.
                    raise LLMError(f'{self.name} HTTP {response.status_code}: '
                                   f'{_safe_snippet(response.text)}')
            attempt += 1
            if attempt <= self.max_retries:
                time.sleep(self.backoff * (2 ** (attempt - 1)))
        raise last_error or LLMError(f'{self.name} failed')


def _safe_snippet(text: str, limit: int = 200) -> str:
    """Provider error bodies can echo the request. Redact before surfacing."""
    from ..logging import redact
    return str(redact(text or ''))[:limit]


def elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
