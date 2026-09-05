"""Test-only TTS.  Emits a valid but silent WAV so audio plumbing can be tested.

Never selected in production: the router accepts it only when
``SMRITI_TTS_PROVIDER=mock`` is set explicitly.
"""
from __future__ import annotations

import struct
import time

from ..schemas import TTSResult

SAMPLE_RATE = 16000


def silent_wav(duration_s: float = 0.25, sample_rate: int = SAMPLE_RATE) -> bytes:
    """A real, parseable 16-bit PCM WAV containing silence."""
    frames = int(duration_s * sample_rate)
    data = b'\x00\x00' * frames
    header = b'RIFF' + struct.pack('<I', 36 + len(data)) + b'WAVE'
    header += b'fmt ' + struct.pack('<IHHIIHH', 16, 1, 1, sample_rate, sample_rate * 2, 2, 16)
    header += b'data' + struct.pack('<I', len(data))
    return header + data


class MockTTSProvider:
    name = 'mock'
    online = False

    def __init__(self, languages: frozenset[str] | None = None) -> None:
        self.languages = languages
        self.calls: list[tuple[str, str]] = []

    def supports(self, language: str) -> bool:
        return self.languages is None or language in self.languages

    def synthesize(self, text: str, language: str, *, voice: str | None = None) -> TTSResult:
        started = time.perf_counter()
        self.calls.append((language, text))
        return TTSResult(audio=silent_wav(), language=language, voice=voice or 'mock',
                         provider=self.name, model='mock', offline=True, sample_rate=SAMPLE_RATE,
                         latency_ms=int((time.perf_counter() - started) * 1000))
