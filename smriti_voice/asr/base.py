"""ASR provider contract.

``BaseASR`` itself lives in :mod:`smriti_voice.asr.providers` (moved verbatim from
v4.1 so the production classes are untouched).  This module adds the structural
protocol used by the new code and by tests.
"""
from __future__ import annotations

from pathlib import Path
from typing import Protocol, runtime_checkable


@runtime_checkable
class ASRProvider(Protocol):
    """Anything that can turn a WAV file into text.

    Implementations return ``(transcript, language_code, language_confidence)``.
    This is the tuple shape v4.1 providers already return, kept for compatibility.
    """

    online: bool
    name: str

    def warmup(self) -> None: ...

    def transcribe(self, wav: Path, language: str | None = None) -> tuple[str, str | None, float]: ...
