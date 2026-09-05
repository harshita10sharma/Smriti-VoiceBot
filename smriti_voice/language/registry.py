"""The one place that knows about languages.

Wraps the v4.1 :class:`~smriti_voice.config.LanguageRegistry` (which owns
``language_packs/ner_languages.json``) and adds the capability matrix.  No other
module should hard-code a language code or a BCP-47 tag.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..config import LanguagePack, LanguageRegistry as PackRegistry, Settings
from ..exceptions import LanguageNotSupported
from ..schemas import LanguageCapability, LanguageStatus
from .capabilities import (
    SARVAM_ASR_LANGUAGES,
    SARVAM_TTS_LANGUAGES,
    SARVAM_TTS_VOICES,
    build_capability,
    load_validation_records,
)

# Mapping from provider/ISO codes back to the internal three-letter codes.
ISO_ALIASES: dict[str, str] = {
    'en': 'eng', 'hi': 'hin', 'as': 'asm', 'bn': 'ben', 'brx': 'brx', 'mni': 'mni',
    'ne': 'npi', 'gu': 'guj', 'mr': 'mar', 'od': 'ori', 'or': 'ori', 'pa': 'pan',
    'ta': 'tam', 'te': 'tel', 'kn': 'kan', 'ml': 'mal', 'ur': 'urd', 'kok': 'kok',
    'ks': 'ksm', 'sd': 'snd', 'sa': 'san', 'sat': 'sat', 'doi': 'doi', 'njz': 'nyish',
}


def normalise_code(code: str | None) -> str | None:
    """Turn ``hi-IN`` / ``hi`` / ``njz`` into the internal code, or None."""
    if not code:
        return None
    raw = code.strip()
    if not raw or raw == 'auto':
        return None
    if raw.endswith('-IN'):
        raw = raw[:-3]
    lowered = raw.lower()
    return ISO_ALIASES.get(lowered, lowered)


class LanguageService:
    """Capability lookups used by ASR, TTS, the LLM router and the API."""

    def __init__(self, pack_dir: Path | None = None) -> None:
        settings_dir = pack_dir or Settings.load().language_pack_dir
        self.packs = PackRegistry(Path(settings_dir))
        self._validation = load_validation_records()
        self._matrix: dict[str, LanguageCapability] = {
            pack.code: build_capability(pack, self._validation) for pack in self.packs.all()
        }

    # ------------------------------------------------------------------ #
    def all(self) -> tuple[LanguageCapability, ...]:
        return tuple(self._matrix[code] for code in sorted(self._matrix))

    def codes(self) -> frozenset[str]:
        return frozenset(self._matrix)

    def get(self, code: str) -> LanguageCapability:
        resolved = normalise_code(code) or code
        if resolved not in self._matrix:
            raise LanguageNotSupported(f'{code!r} is not a configured SMRITI language')
        return self._matrix[resolved]

    def pack(self, code: str) -> LanguagePack:
        return self.packs.get(code)

    def is_known(self, code: str | None) -> bool:
        resolved = normalise_code(code) if code else None
        return bool(resolved and resolved in self._matrix)

    # ------------------------------------------------------------------ #
    def can_speak(self, code: str) -> bool:
        """True when a configured TTS provider actually supports this language."""
        try:
            return self.get(code).tts_online
        except LanguageNotSupported:
            return False

    def tts_language_tag(self, code: str) -> str | None:
        return SARVAM_TTS_LANGUAGES.get(normalise_code(code) or code)

    def tts_voice(self, code: str) -> str | None:
        return SARVAM_TTS_VOICES.get(normalise_code(code) or code)

    def asr_language_tag(self, code: str) -> str | None:
        return SARVAM_ASR_LANGUAGES.get(normalise_code(code) or code)

    def llm_supported(self, code: str) -> bool:
        try:
            return self.get(code).llm_support
        except LanguageNotSupported:
            return False

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in LanguageStatus}
        for capability in self._matrix.values():
            counts[capability.status.value] += 1
        return counts


@lru_cache(maxsize=2)
def get_language_service() -> LanguageService:
    return LanguageService()
