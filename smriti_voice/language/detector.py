"""Language detection.

Three signals, cheapest first:

1. The provider's own detection (Sarvam returns ``language_code`` + probability).
2. NE-LID, which covers 11 Northeast languages the cloud providers do not.
3. Unicode script detection, which is offline, dependency-free and decisive for
   Devanagari vs Bengali-Assamese vs Latin.

The result is only used to *route*.  Nothing executes because of it.
"""
from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..lid import LIDError, NELID
from .registry import LanguageService, normalise_code

# Script → most likely internal code.  Assamese and Bengali share a script, so
# script alone can never separate them; the tie-break uses Assamese-only letters.
SCRIPT_DEFAULTS: dict[str, str] = {
    'DEVANAGARI': 'hin',
    'BENGALI': 'ben',
    'LATIN': 'eng',
    'GUJARATI': 'guj',
    'ORIYA': 'ori',
    'GURMUKHI': 'pan',
    'TAMIL': 'tam',
    'TELUGU': 'tel',
    'KANNADA': 'kan',
    'MALAYALAM': 'mal',
    'ARABIC': 'urd',
    'MEETEI': 'mni',
    'OL': 'sat',
    'CHAKMA': 'ccp',
}

# 'ৰ' (RA WITH MIDDLE DIAGONAL) and 'ৱ' (WA) exist in Assamese but not Bengali.
ASSAMESE_MARKERS = frozenset('ৰৱ')


@dataclass(frozen=True)
class DetectionResult:
    language: str
    confidence: float
    method: str


def detect_script(text: str) -> tuple[str | None, float]:
    """Dominant Unicode script of the text and the fraction of letters in it."""
    counts: dict[str, int] = {}
    letters = 0
    for char in text or '':
        if not char.isalpha():
            continue
        letters += 1
        try:
            name = unicodedata.name(char)
        except ValueError:
            continue
        script = name.split()[0]
        counts[script] = counts.get(script, 0) + 1
    if not letters or not counts:
        return None, 0.0
    script, count = max(counts.items(), key=lambda kv: kv[1])
    return script, count / letters


class LanguageDetector:
    """Combines provider hints, NE-LID and script detection."""

    def __init__(self, languages: LanguageService, lid: NELID | None = None) -> None:
        self.languages = languages
        self.lid = lid or NELID()

    def detect(self, text: str, *, provider_hint: str | None = None,
               provider_confidence: float = 0.0) -> DetectionResult:
        # 1. Trust a confident provider hint for a language we actually configure.
        hint = normalise_code(provider_hint)
        if hint and self.languages.is_known(hint) and provider_confidence >= 0.80:
            return DetectionResult(hint, provider_confidence, 'provider')

        # 2. NE-LID for the Northeast languages nothing else covers.
        try:
            lid_code, lid_score = self.lid.predict(text)
            resolved = normalise_code(lid_code)
            if resolved and self.languages.is_known(resolved) and lid_score >= 0.80:
                return DetectionResult(resolved, float(lid_score), 'ne_lid')
        except (LIDError, Exception):  # NE-LID is optional at runtime
            pass

        # 3. Script detection: always available, no network, no model.
        script, ratio = detect_script(text)
        if script:
            code = SCRIPT_DEFAULTS.get(script)
            if code == 'ben' and ASSAMESE_MARKERS & set(text or ''):
                code = 'asm'
            if code and self.languages.is_known(code):
                return DetectionResult(code, round(min(ratio, 0.75), 4), 'script')

        # 4. A weak provider hint is still better than nothing.
        if hint and self.languages.is_known(hint):
            return DetectionResult(hint, provider_confidence, 'provider_weak')

        return DetectionResult('eng', 0.0, 'default')
