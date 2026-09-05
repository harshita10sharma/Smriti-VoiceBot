"""TTS provider contract."""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from ..schemas import TTSResult


@runtime_checkable
class TTSProvider(Protocol):
    name: str
    online: bool

    def supports(self, language: str) -> bool: ...

    def synthesize(self, text: str, language: str, *, voice: str | None = None) -> TTSResult: ...
