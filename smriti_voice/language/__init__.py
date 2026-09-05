"""Language registry, capability matrix and detection."""
from .capabilities import (
    LLM_LANGUAGES,
    NE_LID_LANGUAGES,
    SARVAM_ASR_LANGUAGES,
    SARVAM_TTS_LANGUAGES,
    build_capability,
)
from .detector import DetectionResult, LanguageDetector, detect_script
from .registry import LanguageService, get_language_service, normalise_code

__all__ = [
    'LLM_LANGUAGES', 'NE_LID_LANGUAGES', 'SARVAM_ASR_LANGUAGES', 'SARVAM_TTS_LANGUAGES',
    'build_capability', 'DetectionResult', 'LanguageDetector', 'detect_script',
    'LanguageService', 'get_language_service', 'normalise_code',
]
