"""Configuration, schemas, logging, language registry, memory and providers."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from smriti_voice.config import AppConfig, LanguageRegistry, Settings
from smriti_voice.language.capabilities import (
    SARVAM_ASR_LANGUAGES,
    SARVAM_TTS_LANGUAGES,
    _status_for,
)
from smriti_voice.language.detector import LanguageDetector, detect_script
from smriti_voice.language.registry import LanguageService, normalise_code
from smriti_voice.logging import redact
from smriti_voice.schemas import ConversationRequest, LanguageStatus, TTSResult
from smriti_voice.memory.provenance import caveat_for, is_quotable, provenance_for_write


# --------------------------------------------------------------------------- #
# Configuration and secret handling
# --------------------------------------------------------------------------- #
def test_v41_settings_still_load():
    settings = Settings.load()
    assert settings.sample_rate == 16000 and settings.api_key_env == 'SMRITI_API_KEY'


def test_app_config_never_exposes_key_values(monkeypatch):
    monkeypatch.setenv('SARVAM_API_KEY', 'sk_super_secret_value')
    config = AppConfig.load()
    exposed = config.providers.configured_providers()
    assert exposed == {'sarvam': True, 'gemini': False, 'openai': False}
    assert 'sk_super_secret_value' not in str(exposed)


@pytest.mark.parametrize('payload,expected_absent', [
    ({'SARVAM_API_KEY': 'sk_abc123456789'}, 'sk_abc123456789'),
    ({'authorization': 'Bearer abcdef123456'}, 'abcdef123456'),
    ({'note': 'key is AIzaSyABCDEFGHIJKLMNOPQRSTUVWXYZ123'}, 'AIzaSyABCDEFGH'),
])
def test_redaction_removes_secrets(payload, expected_absent):
    assert expected_absent not in str(redact(payload))


def test_redaction_drops_audio_payloads():
    assert 'audio' not in redact({'audio': b'RIFF....', 'transcript': 'hello'})


# --------------------------------------------------------------------------- #
# Schemas
# --------------------------------------------------------------------------- #
def test_conversation_request_rejects_injection_in_identifiers():
    for bad in ['../../etc', 'user; DROP TABLE', 'a b', "u'--"]:
        with pytest.raises(ValidationError):
            ConversationRequest(user_id=bad, message='hello')


def test_conversation_request_rejects_oversized_message():
    with pytest.raises(ValidationError):
        ConversationRequest(user_id='u1', message='x' * 5000)


def test_tts_audio_bytes_are_never_serialised():
    assert 'audio' not in TTSResult(audio=b'12345').model_dump()


# --------------------------------------------------------------------------- #
# Language registry and capability matrix
# --------------------------------------------------------------------------- #
def test_registry_still_loads_all_v41_packs():
    assert len(LanguageRegistry(Settings.load().language_pack_dir).all()) == 15


def test_no_language_claims_support_without_measured_evidence():
    for capability in LanguageService().all():
        if not capability.validated:
            assert capability.status is not LanguageStatus.SUPPORTED, (
                f'{capability.code} claims SUPPORTED with no validation record')


def test_status_reaches_supported_only_with_validation():
    common = dict(asr_online=True, asr_offline=True, tts=True, llm=True,
                  pack_status='validated_local')
    assert _status_for(**common, validated=False) is LanguageStatus.NOT_YET_TESTED
    assert _status_for(**common, validated=True) is LanguageStatus.SUPPORTED


def test_assamese_has_asr_but_no_configured_voice():
    """Sarvam Bulbul does not document Assamese. The matrix must say so."""
    assamese = LanguageService().get('asm')
    assert assamese.asr_online is True
    assert assamese.tts_online is False
    assert 'asm' in SARVAM_ASR_LANGUAGES and 'asm' not in SARVAM_TTS_LANGUAGES
    assert any('Assamese' in note for note in assamese.known_limitations)


def test_tts_language_list_is_a_subset_of_asr_language_list():
    assert set(SARVAM_TTS_LANGUAGES) < set(SARVAM_ASR_LANGUAGES)


@pytest.mark.parametrize('given,expected', [
    ('hi-IN', 'hin'), ('hi', 'hin'), ('en-IN', 'eng'), ('as', 'asm'), ('bn', 'ben'),
    ('njz', 'nyish'), ('auto', None), ('', None), (None, None),
])
def test_language_code_normalisation(given, expected):
    assert normalise_code(given) == expected


@pytest.mark.parametrize('text,script', [
    ('how are you', 'LATIN'), ('आप कैसे हैं', 'DEVANAGARI'), ('আপনি কেমন আছেন', 'BENGALI'),
])
def test_script_detection(text, script):
    assert detect_script(text)[0] == script


def test_detector_separates_assamese_by_its_own_letters():
    detector = LanguageDetector(LanguageService())
    assert detector.detect('আপোনাৰ নাম কি').language == 'asm'   # contains ৰ
    assert detector.detect('আপনার নাম কি').language == 'ben'


def test_unknown_language_is_rejected_not_guessed():
    from smriti_voice.exceptions import LanguageNotSupported
    with pytest.raises(LanguageNotSupported):
        LanguageService().get('klingon')


# --------------------------------------------------------------------------- #
# Provenance
# --------------------------------------------------------------------------- #
def test_assistant_written_facts_are_never_trusted():
    provenance = provenance_for_write(source='assistant')
    assert provenance.verification_status == 'unverified'
    assert not is_quotable(provenance)
    assert provenance.confidence <= 0.5
    assert caveat_for(provenance, 'eng')


def test_caregiver_facts_are_trusted():
    provenance = provenance_for_write(source='caregiver')
    assert is_quotable(provenance) and caveat_for(provenance) is None


# --------------------------------------------------------------------------- #
# Memory
# --------------------------------------------------------------------------- #
def test_relative_dates(app):
    from smriti_voice.memory.service import resolve_date
    assert resolve_date('yesterday', today=date(2026, 9, 5)).value == date(2026, 9, 4)
    assert resolve_date('2026-01-02').value == date(2026, 1, 2)
    # Nonsense input falls back to today rather than raising at the user.
    assert resolve_date('sometime last winter', today=date(2026, 9, 5)).value == date(2026, 9, 5)


def test_lexical_retrieval_finds_the_right_memory(app):
    results = app.memory.search_memories('demo-user', 'tell me about the wedding')['results']
    assert results and results[0]['title'] == "Bina's wedding"


def test_retrieval_returns_nothing_rather_than_a_bad_match(app):
    assert app.memory.search_memories('demo-user', 'quantum chromodynamics')['count'] == 0
