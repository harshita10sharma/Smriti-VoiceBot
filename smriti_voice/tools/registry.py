"""The tool registry: the only path from a model's suggestion to real work.

Execution order is fixed and every step can refuse:

    LLM proposes  →  name lookup  →  schema validation  →  safety screening
                  →  authorisation  →  handler  →  typed result

An unknown name, an extra argument, a sensitive tool or a cross-user argument all
fail closed and return a ``ToolResult`` with ``executed=False``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

from pydantic import BaseModel, ValidationError

from ..logging import get_logger
from ..safety.authorization import Authorizer, Principal
from ..safety.policy import SafetyPolicy
from ..schemas import PermissionLevel, SafetyLevel, ToolCall, ToolResult
from .schemas import StrictModel

log = get_logger('tools.registry')

# The handler receives (principal, validated_args, context) and returns a dict.
Handler = Callable[[Principal, BaseModel, 'ToolContext'], dict[str, Any]]


@dataclass
class ToolContext:
    """Everything a handler may touch.  Handlers get no globals."""
    memory: Any                      # MemoryService
    weather: Any = None              # WeatherProvider | None
    language: str = 'eng'
    request_id: str = ''
    session_id: str = ''
    extras: dict[str, Any] | None = None


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Handler
    safety_level: SafetyLevel = SafetyLevel.READ_ONLY
    permission: PermissionLevel = PermissionLevel.USER
    action: str | None = None          # deterministic action this tool maps onto
    requires_confirmation: bool = False

    def json_schema(self) -> dict[str, Any]:
        """JSON Schema as advertised to the model (no pydantic-only keys)."""
        schema = self.args_model.model_json_schema()
        schema.pop('title', None)
        for prop in (schema.get('properties') or {}).values():
            prop.pop('title', None)
        schema.setdefault('type', 'object')
        schema.setdefault('properties', {})
        return schema


class ToolRegistry:
    def __init__(self, policy: SafetyPolicy, authorizer: Authorizer | None = None) -> None:
        self.policy = policy
        self.authorizer = authorizer or Authorizer()
        self._tools: dict[str, Tool] = {}

    # ------------------------------------------------------------------ #
    def register(self, tool: Tool) -> Tool:
        if tool.name in self._tools:
            raise ValueError(f'Tool {tool.name!r} is already registered')
        self._tools[tool.name] = tool
        return tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def all(self) -> tuple[Tool, ...]:
        return tuple(self._tools[name] for name in self.names())

    def specs(self, *, include_actions: bool = True) -> list:
        """Tool specs for the LLM.  Sensitive tools are never advertised."""
        from ..llm.base import ToolSpec
        return [
            ToolSpec(name=tool.name, description=tool.description, parameters=tool.json_schema())
            for tool in self.all()
            if tool.safety_level is not SafetyLevel.SENSITIVE
            and (include_actions or tool.safety_level is SafetyLevel.READ_ONLY)
        ]

    # ------------------------------------------------------------------ #
    def execute(self, call: ToolCall, principal: Principal, context: ToolContext,
                *, confirmed: bool = False) -> ToolResult:
        """Validate, screen, authorise and run one proposed tool call.

        ``confirmed=True`` is set only by the conversation manager after the user
        has said yes to a stored :class:`PendingConfirmation`. It skips step 4;
        every other gate still runs.
        """
        started = time.perf_counter()
        name = (call.name or '').strip()

        tool = self._tools.get(name)
        if tool is None:
            log.warning('tool_unknown', fields={'request_id': context.request_id, 'tool': name})
            return ToolResult(name=name, ok=False, error=f'Unknown tool: {name!r}',
                              error_code='TOOL_NOT_FOUND', executed=False,
                              latency_ms=_ms(started))

        # 1. Schema validation. Extra/missing/ill-typed arguments are rejected.
        try:
            arguments = tool.args_model.model_validate(call.arguments or {})
        except ValidationError as exc:
            log.info('tool_invalid_arguments',
                     fields={'request_id': context.request_id, 'tool': name,
                             'errors': [e['loc'] for e in exc.errors()]})
            return ToolResult(name=name, ok=False, error=_readable_validation_error(exc),
                              error_code='TOOL_VALIDATION_ERROR', executed=False,
                              safety_level=tool.safety_level, latency_ms=_ms(started))

        # 2. Deterministic safety screening of the tool and its argument values.
        verdict = self.policy.screen_tool_call(name, arguments.model_dump(), tool.safety_level)
        if not verdict.allowed:
            log.warning('tool_refused_by_policy',
                        fields={'request_id': context.request_id, 'tool': name,
                                'reason': verdict.reason})
            return ToolResult(name=name, ok=False, error=verdict.reason,
                              error_code='SAFETY_REFUSAL', executed=False,
                              safety_level=tool.safety_level, latency_ms=_ms(started))

        # 3. Authorisation.
        granted = self.authorizer.authorize(
            principal, tool_name=name, required_permission=tool.permission,
            safety_level=tool.safety_level,
            target_user_id=getattr(arguments, 'user_id', None))
        if not granted.allowed:
            return ToolResult(name=name, ok=False, error=granted.reason,
                              error_code='NOT_AUTHORIZED', executed=False,
                              safety_level=tool.safety_level, latency_ms=_ms(started))

        # 4. A controlled action stops here and asks the user first.
        if tool.requires_confirmation and not confirmed:
            return ToolResult(name=name, ok=True, data={'pending': True,
                                                        'action': tool.action,
                                                        'arguments': arguments.model_dump()},
                              safety_level=tool.safety_level, executed=False,
                              requires_confirmation=True, latency_ms=_ms(started))

        # 5. Execute.
        try:
            data = tool.handler(principal, arguments, context)
        except Exception as exc:
            log.warning('tool_handler_failed',
                        fields={'request_id': context.request_id, 'tool': name,
                                'error': type(exc).__name__})
            return ToolResult(name=name, ok=False, error=f'{type(exc).__name__}',
                              error_code='TOOL_ERROR', executed=False,
                              safety_level=tool.safety_level, latency_ms=_ms(started))

        return ToolResult(name=name, ok=True, data=data, safety_level=tool.safety_level,
                          executed=True, latency_ms=_ms(started))


def _readable_validation_error(exc: ValidationError) -> str:
    parts = []
    for error in exc.errors()[:4]:
        location = '.'.join(str(item) for item in error['loc']) or 'arguments'
        parts.append(f'{location}: {error["msg"]}')
    return '; '.join(parts)


def _ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
