"""Deterministic in-process LLM for tests and offline demos.

It is NOT a language model.  It follows a scripted policy so that conversation,
tool-calling and safety logic can be tested without a network or a key.  It is
never selected automatically in production: the router uses it only when
``SMRITI_LLM_PROVIDER=mock`` is set explicitly.
"""
from __future__ import annotations

import time
from typing import Any, Callable

from ..schemas import LLMResponse, ToolCall
from .base import Message, ToolSpec, elapsed_ms


class MockLLMProvider:
    name = 'mock'
    online = False
    supports_tools = True

    def __init__(self, *, script: list[LLMResponse] | None = None,
                 handler: Callable[[list[Message], list[ToolSpec] | None], LLMResponse] | None = None,
                 reply: str = 'Yes, I am here to help you.') -> None:
        self._script = list(script or [])
        self._handler = handler
        self._reply = reply
        self.calls: list[dict[str, Any]] = []

    def generate(self, messages: list[Message], *, system: str = '',
                 tools: list[ToolSpec] | None = None,
                 temperature: float = 0.2, max_tokens: int = 512) -> LLMResponse:
        started = time.perf_counter()
        self.calls.append({'messages': list(messages), 'system': system,
                           'tools': [t.name for t in tools or []]})
        if self._script:
            response = self._script.pop(0)
        elif self._handler:
            response = self._handler(messages, tools)
        else:
            response = LLMResponse(text=self._reply)
        return response.model_copy(update={'provider': self.name, 'model': 'mock',
                                           'offline': True, 'latency_ms': elapsed_ms(started)})


def tool_call_response(tool_name: str, /, **arguments: Any) -> LLMResponse:
    """Convenience for tests: 'the model asked for this tool'.

    ``tool_name`` is positional-only so a tool argument may itself be called
    ``name`` (``tool_call_response('call_family_member', name='Bina')``).
    """
    return LLMResponse(text='', tool_calls=[ToolCall(name=tool_name, arguments=arguments)])
