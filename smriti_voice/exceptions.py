"""Typed exceptions.  Every failure the user can trigger has a stable code."""
from __future__ import annotations


class SmritiError(RuntimeError):
    """Base class.  ``code`` is what telemetry and the API surface."""
    code = 'SMRITI_ERROR'

    def __init__(self, message: str = '', *, code: str | None = None) -> None:
        super().__init__(message or self.__class__.__name__)
        if code:
            self.code = code


class ConfigurationError(SmritiError):
    code = 'CONFIGURATION_ERROR'


class ProviderError(SmritiError):
    """A provider (ASR/LLM/TTS/weather) failed or is not configured."""
    code = 'PROVIDER_ERROR'


class ProviderNotConfigured(ProviderError):
    code = 'PROVIDER_NOT_CONFIGURED'


class ProviderTimeout(ProviderError):
    code = 'PROVIDER_TIMEOUT'


class LLMError(ProviderError):
    code = 'LLM_ERROR'


class TTSError(ProviderError):
    code = 'TTS_ERROR'


class WeatherError(ProviderError):
    code = 'WEATHER_ERROR'


class AudioError(SmritiError):
    code = 'AUDIO_ERROR'


class LanguageNotSupported(SmritiError):
    code = 'LANGUAGE_NOT_SUPPORTED'


class ToolError(SmritiError):
    code = 'TOOL_ERROR'


class ToolNotFound(ToolError):
    code = 'TOOL_NOT_FOUND'


class ToolValidationError(ToolError):
    code = 'TOOL_VALIDATION_ERROR'


class SafetyRefusal(SmritiError):
    """Raised when the deterministic safety layer refuses to proceed."""
    code = 'SAFETY_REFUSAL'


class AuthorizationError(SmritiError):
    code = 'NOT_AUTHORIZED'


class MemoryNotFound(SmritiError):
    code = 'MEMORY_NOT_FOUND'
