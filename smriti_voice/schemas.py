"""Typed domain objects shared by every subsystem.

Pydantic is used so that anything crossing a trust boundary (an LLM response, an
HTTP request, a tool argument) is validated rather than duck-typed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_MESSAGE_CHARS = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------- #
# Enumerations
# --------------------------------------------------------------------------- #
class ExecutionMode(str, Enum):
    """How the turn was (or will be) served."""
    ONLINE_PRIMARY = 'ONLINE_PRIMARY'
    OFFLINE_PRIMARY = 'OFFLINE_PRIMARY'
    DEGRADED = 'DEGRADED'
    ERROR = 'ERROR'


class VoiceState(str, Enum):
    IDLE = 'IDLE'
    LISTENING = 'LISTENING'
    PROCESSING = 'PROCESSING'
    SPEAKING = 'SPEAKING'
    ERROR = 'ERROR'


class TurnKind(str, Enum):
    """Which branch of the turn router produced the answer."""
    COMMAND = 'COMMAND'                # deterministic v4.1 router accepted
    CONVERSATION = 'CONVERSATION'      # LLM answered
    MEMORY = 'MEMORY'                  # answered from a personal-memory tool
    CONFIRMATION = 'CONFIRMATION'      # resolved a pending confirmation
    REFUSAL = 'REFUSAL'                # safety layer refused
    FALLBACK = 'FALLBACK'              # deterministic offline answer, no LLM
    ERROR = 'ERROR'


class SafetyLevel(str, Enum):
    READ_ONLY = 'READ_ONLY'                    # never mutates anything
    CONTROLLED_ACTION = 'CONTROLLED_ACTION'    # mutates, needs confirmation
    SENSITIVE = 'SENSITIVE'                    # caregiver-only, never executed by voice


class PermissionLevel(str, Enum):
    USER = 'USER'
    CAREGIVER = 'CAREGIVER'
    SYSTEM = 'SYSTEM'


class LanguageStatus(str, Enum):
    SUPPORTED = 'SUPPORTED'
    SUPPORTED_WITH_LIMITATIONS = 'SUPPORTED_WITH_LIMITATIONS'
    BENCHMARK_ONLY = 'BENCHMARK_ONLY'
    ONLINE_ONLY = 'ONLINE_ONLY'
    OFFLINE_ONLY = 'OFFLINE_ONLY'
    UNSUPPORTED = 'UNSUPPORTED'
    NOT_YET_TESTED = 'NOT_YET_TESTED'


# --------------------------------------------------------------------------- #
# Provider results
# --------------------------------------------------------------------------- #
class ASRResult(BaseModel):
    transcript: str = ''
    language: str | None = None
    language_confidence: float = 0.0
    provider: str = 'none'
    model: str | None = None
    offline: bool = False
    latency_ms: int = 0


class TTSResult(BaseModel):
    """TTS output.  ``audio`` is raw bytes and is deliberately excluded from dumps."""
    model_config = ConfigDict(arbitrary_types_allowed=True)

    audio: bytes | None = Field(default=None, exclude=True, repr=False)
    audio_id: str | None = None
    mime_type: str = 'audio/wav'
    sample_rate: int = 22050
    language: str = 'eng'
    voice: str | None = None
    provider: str = 'none'
    model: str | None = None
    offline: bool = False
    latency_ms: int = 0
    available: bool = True
    unavailable_reason: str | None = None

    @property
    def size_bytes(self) -> int:
        return len(self.audio or b'')


class ToolCall(BaseModel):
    """A tool request *proposed* by the model.  Never trusted until validated."""
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    call_id: str | None = None
    # Opaque, provider-specific continuation token (currently only Gemini's
    # thoughtSignature). Ignored by every other provider; must be replayed
    # verbatim on the next turn or Gemini's multi-round tool calling breaks.
    thought_signature: str | None = None

    @field_validator('name')
    @classmethod
    def _clean_name(cls, v: str) -> str:
        return (v or '').strip()


class ToolResult(BaseModel):
    name: str
    ok: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    error_code: str | None = None
    safety_level: SafetyLevel = SafetyLevel.READ_ONLY
    executed: bool = False
    latency_ms: int = 0
    requires_confirmation: bool = False
    confirmation_prompt: str | None = None


class LLMResponse(BaseModel):
    text: str = ''
    tool_calls: list[ToolCall] = Field(default_factory=list)
    provider: str = 'none'
    model: str | None = None
    offline: bool = False
    latency_ms: int = 0
    finish_reason: str | None = None


class SafetyDecision(BaseModel):
    allowed: bool
    reason: str
    category: str | None = None
    refusal_key: str | None = None
    matched: list[str] = Field(default_factory=list)


class LanguageCapability(BaseModel):
    """One row of the language capability matrix.  No claim without a status."""
    code: str
    name: str
    script: str = ''
    asr_provider_online: str | None = None
    asr_model_online: str | None = None
    asr_provider_offline: str | None = None
    asr_model_offline: str | None = None
    asr_online: bool = False
    asr_offline: bool = False
    llm_support: bool = False
    tts_provider: str | None = None
    tts_online: bool = False
    tts_offline: bool = False
    tts_voice: str | None = None
    language_detection: bool = False
    status: LanguageStatus = LanguageStatus.NOT_YET_TESTED
    benchmark_status: str = 'not_benchmarked'
    validated: bool = False
    known_limitations: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Conversation
# --------------------------------------------------------------------------- #
class ConversationTurn(BaseModel):
    turn_id: str
    session_id: str
    user_id: str
    role: Literal['user', 'assistant'] = 'user'
    text: str = ''
    language: str = 'eng'
    kind: TurnKind = TurnKind.CONVERSATION
    created_at: datetime = Field(default_factory=_utcnow)


class PendingConfirmation(BaseModel):
    """A controlled action waiting for an explicit yes.  Expires."""
    action: str
    tool_name: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    prompt: str = ''
    language: str = 'eng'
    created_at: datetime = Field(default_factory=_utcnow)
    expires_in_turns: int = 2


class ConversationRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)
    language: str | None = Field(default=None, max_length=16)

    @field_validator('user_id', 'session_id')
    @classmethod
    def _safe_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not all(ch.isalnum() or ch in '-_.' for ch in v):
            raise ValueError('identifier may only contain letters, digits, -, _ and .')
        return v


class TurnMetadata(BaseModel):
    """Everything measurable about one turn.  Mirrors the telemetry record."""
    request_id: str
    session_id: str
    kind: TurnKind
    execution_mode: ExecutionMode = ExecutionMode.DEGRADED
    offline: bool = False
    fallback_used: bool = False
    asr_provider: str | None = None
    asr_latency_ms: int = 0
    llm_provider: str | None = None
    llm_latency_ms: int = 0
    tool_latency_ms: int = 0
    tts_provider: str | None = None
    tts_latency_ms: int = 0
    total_latency_ms: int = 0
    error_code: str | None = None


class ConversationResponse(BaseModel):
    request_id: str
    session_id: str
    response_text: str
    language: str
    language_confidence: float = 0.0
    kind: TurnKind = TurnKind.CONVERSATION
    action: str = 'NO_ACTION'
    action_accepted: bool = False
    tool_calls: list[ToolCall] = Field(default_factory=list)
    tool_results: list[ToolResult] = Field(default_factory=list)
    safety: SafetyDecision | None = None
    requires_confirmation: bool = False
    metadata: TurnMetadata


class VoiceResponse(ConversationResponse):
    transcript: str = ''
    audio_id: str | None = None
    audio_url: str | None = None
    audio_available: bool = False
    audio_unavailable_reason: str | None = None
    tts_provider: str | None = None
