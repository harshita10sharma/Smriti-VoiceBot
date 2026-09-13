"""Typed domain objects shared by every subsystem.

Pydantic is used so that anything crossing a trust boundary (an LLM response, an
HTTP request, a tool argument) is validated rather than duck-typed.
"""
from __future__ import annotations

import uuid
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
    WELCOME = 'WELCOME'                # deterministic proactive greeting, no LLM, no user turn


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
    # A stable id for this specific proposal instance, independent of
    # session_id (one session has at most one pending confirmation at a
    # time, but a client that logs/correlates across the propose and
    # confirm turns benefits from an explicit id rather than inferring
    # "the one pending action" implicitly). Surfaced to the API as
    # ConversationResponse.metadata.action_id.
    action_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
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


class WelcomeRequest(BaseModel):
    """POST /v1/conversation/welcome -- open the app, get a proactive first
    message with no meaningless ASR/text turn required. Never consumes a
    user turn: no ``message`` field exists because none is expected."""
    user_id: str = Field(min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=64)
    language: str | None = Field(default=None, max_length=16)
    speak: bool = False

    @field_validator('user_id', 'session_id')
    @classmethod
    def _safe_id(cls, v: str | None) -> str | None:
        if v is None:
            return v
        if not all(ch.isalnum() or ch in '-_.' for ch in v):
            raise ValueError('identifier may only contain letters, digits, -, _ and .')
        return v


class WelcomeResponse(BaseModel):
    request_id: str
    session_id: str
    response_text: str
    language: str
    kind: TurnKind = TurnKind.WELCOME
    session_restored: bool = False
    # Same async-TTS shape as VoiceResponse, so a client already handling
    # that flow needs no new logic to handle this one.
    job_id: str | None = None
    job_status: str = 'NOT_REQUESTED'
    audio_id: str | None = None
    audio_url: str | None = None
    audio_available: bool = False
    audio_unavailable_reason: str | None = None
    tts_provider: str | None = None


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
    # 'personal' | 'general' | None -- see conversation/classifier.py. Purely
    # observational (telemetry/debugging); additive field, never read by any
    # existing client.
    topic: str | None = None
    # The PendingConfirmation.action_id for this turn's proposal or
    # resolution, if this turn touched one; None for a turn with no
    # controlled action involved at all. Additive field: a client that
    # ignores it sees exactly the same contract as before this existed.
    action_id: str | None = None


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
    # TTS runs asynchronously: audio_id/audio_available/tts_provider are
    # never populated on this response. Poll GET /v1/voice/jobs/{job_id}
    # (job_id is set whenever speak=True and text was produced) until
    # status is 'completed', then GET /v1/audio/{audio_id}.
    job_id: str | None = None
    job_status: str = 'NOT_REQUESTED'  # QUEUED | PROCESSING | COMPLETED | FAILED | NOT_REQUESTED
    audio_id: str | None = None
    audio_url: str | None = None
    audio_available: bool = False
    audio_unavailable_reason: str | None = None
    tts_provider: str | None = None


class VoiceJobStatusResponse(BaseModel):
    """GET /v1/voice/jobs/{job_id}."""
    job_id: str
    status: str  # queued | processing | completed | failed | cancelled
    language: str
    audio_id: str | None = None
    audio_url: str | None = None
    tts_provider: str | None = None
    error_code: str | None = None
    # True only when status == 'completed' but the audio file has since
    # aged out of retention -- lets a client distinguish "never generated"
    # from "was generated, now gone" without a separate failed GET
    # /v1/audio/{id} round trip. See tts/router.py's AudioStore retention.
    audio_expired: bool = False


class VoiceJobCancelResponse(BaseModel):
    """POST /v1/voice/jobs/{job_id}/cancel."""
    job_id: str
    status: str
    cancelled: bool


# --------------------------------------------------------------------------- #
# Caregiver memory synchronisation (POST /v1/memory/sync)
# --------------------------------------------------------------------------- #
# extra='forbid' is deliberate here, beyond the usual API convention: it is
# what rejects a raw phone/phone_number/mobile/etc. field outright, rather
# than silently ignoring it. This endpoint must never accept a real number.
class MemorySyncFamilyMember(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    relationship: str = Field(min_length=1, max_length=40)
    phone_available: bool = False
    # A stable reference the backend assigns (e.g. a Supabase row id),
    # independent of this repository's own autoincrement id. Optional.
    external_id: str | None = Field(default=None, max_length=128)
    memory_prompt: str | None = Field(default=None, max_length=500)
    # Passed straight through to the model as context; never inferred here
    # from absence or from conversation. See conversation/prompts.py.
    is_deceased: bool = False


class MemorySyncMedicine(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    dose: str | None = Field(default=None, max_length=40)
    schedule: str | None = Field(default=None, max_length=120)
    active: bool = True
    external_id: str | None = Field(default=None, max_length=128)
    # Structured schedule, alongside the free-text `dose`/`schedule` above.
    # All optional: a medicine with none of these set is matched only by
    # `schedule`/`time_of_day` text, exactly as before these fields existed.
    chosen_time_min: int | None = Field(default=None, ge=0, le=1439)
    window_start_min: int | None = Field(default=None, ge=0, le=1439)
    window_end_min: int | None = Field(default=None, ge=0, le=1439)
    # Comma-separated ISO weekdays, Monday=1..Sunday=7, e.g. "1,2,3,4,5,6,7".
    days_of_week: str | None = Field(default=None, max_length=20)

    @field_validator('days_of_week')
    @classmethod
    def _valid_days_of_week(cls, v: str | None) -> str | None:
        if v is None:
            return v
        parts = [p.strip() for p in v.split(',') if p.strip()]
        if not parts or any(p not in {'1', '2', '3', '4', '5', '6', '7'} for p in parts):
            raise ValueError('days_of_week must be comma-separated ISO weekdays 1-7 '
                             '(Monday=1, Sunday=7), e.g. "1,2,3,4,5,6,7"')
        return v

    @field_validator('window_end_min')
    @classmethod
    def _window_not_wrapping(cls, v, info):
        start = info.data.get('window_start_min')
        if v is not None and start is not None and v < start:
            raise ValueError('window_end_min must be >= window_start_min '
                             '(non-wrapping window only; see SECURITY.md)')
        return v


class MemorySyncRoutine(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    time: str | None = Field(default=None, max_length=16)
    activity: str = Field(min_length=1, max_length=120)
    external_id: str | None = Field(default=None, max_length=128)

    @field_validator('time')
    @classmethod
    def _valid_time(cls, v: str | None) -> str | None:
        if v is None:
            return v
        import re
        if not re.fullmatch(r'([01]\d|2[0-3]):[0-5]\d', v):
            raise ValueError('time must be 24-hour HH:MM, e.g. "08:00"')
        return v


class MemorySyncRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    user_id: str = Field(min_length=1, max_length=64)
    family_members: list[MemorySyncFamilyMember] = Field(default_factory=list, max_length=200)
    medicines: list[MemorySyncMedicine] = Field(default_factory=list, max_length=200)
    daily_routines: list[MemorySyncRoutine] = Field(default_factory=list, max_length=200)
    # Patient context. All optional: omitting every one of these reproduces
    # the exact original contract (full replace, no revisioning, no
    # external/timezone/language update).
    display_name: str | None = Field(default=None, max_length=120)
    external_id: str | None = Field(default=None, max_length=128)
    timezone: str | None = Field(default=None, max_length=64)
    language_code: str | None = Field(default=None, max_length=16)
    # Revocation, through the existing sync path -- no separate admin
    # endpoint. None (the default) leaves the patient's current active
    # state untouched; explicitly sending false disables every
    # patient-scoped endpoint for this user_id (see
    # api/dependencies.py::ensure_patient_active) without touching API-key
    # authorization, which remains a separate, backend-owned concern.
    # Sending true re-enables a previously disabled patient.
    active: bool | None = Field(default=None)
    # Opt-in revisioning (see MemoryRepository.sync_caregiver_memory). Omit
    # source_revision entirely to get the original unversioned behaviour:
    # every call applies unconditionally, exactly as before this field
    # existed.
    source_revision: int | None = Field(default=None, ge=0)
    schema_version: int = Field(default=1, ge=1)

    @field_validator('user_id')
    @classmethod
    def _safe_user_id(cls, v: str) -> str:
        if not all(ch.isalnum() or ch in '-_.' for ch in v):
            raise ValueError('user_id may only contain letters, digits, -, _ and .')
        return v


class MemorySyncResponse(BaseModel):
    success: bool = True
    user_id: str
    family_members_synced: int
    medicines_synced: int
    daily_routines_synced: int
    # 'applied' (written) | 'no_op' (identical revision+content, nothing
    # written) -- 'stale'/'conflict' never reach this model, they are
    # reported as HTTP 409 with an explanatory body instead.
    status: str = 'applied'
    source_revision: int | None = None
