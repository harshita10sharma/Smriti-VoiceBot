"""Explicit audit of the backend's target-language product codes against
this repository's actual internal codes and provider mappings -- verified
against the current source, not assumed from any prior/external audit
claim (an old claim alleged a Meiteilon-to-Mongolian ASR mapping bug; it
does not reproduce here -- see test_meiteilon_is_never_mapped_to_mongolian
below, which is the actual verification, not a repeat of the claim).

Backend product code -> this repo's internal code (config/AppConfig,
language_packs/ner_languages.json):
    hi  -> hin   (Hindi)
    as  -> asm   (Assamese)
    mni -> mni   (Meiteilon/Manipuri -- same code, no translation needed)
    kha -> kha   (Khasi)
    lus -> lus   (Mizo)
    en  -> eng   (English)
"""
from __future__ import annotations

from smriti_voice.engine import VoiceEngine
from smriti_voice.config import Settings
from smriti_voice.language.capabilities import (
    LLM_LANGUAGES,
    SARVAM_ASR_LANGUAGES,
    SARVAM_TTS_LANGUAGES,
    INDIC_PARLER_TTS_LANGUAGES,
)


def _engine_aliases() -> dict[str, str]:
    """The alias table VoiceEngine.process uses for language-code
    normalization on the auto-detect ASR path (engine.py)."""
    import inspect
    source = inspect.getsource(VoiceEngine.process)
    # Extract the literal aliases dict from source rather than duplicating
    # it by hand, so this test can't silently drift from the real mapping.
    start = source.index('aliases={') + len('aliases=')
    end = source.index('}', start) + 1
    return eval(source[start:end])  # noqa: S307 -- trusted, local source only


def test_meiteilon_is_never_mapped_to_mongolian():
    """The specific claim to verify, not assume: 'mni' must resolve to
    Meiteilon/Manipuri throughout, never to 'mn' (Mongolian's real ISO
    639-1 code) anywhere in this codebase's mapping tables."""
    aliases = _engine_aliases()
    assert aliases.get('mni') == 'mni'
    assert 'mn' not in aliases.values()
    assert SARVAM_ASR_LANGUAGES.get('mni') == 'mni-IN'
    assert 'mn-IN' not in SARVAM_ASR_LANGUAGES.values()


def test_target_language_pack_codes_exist_with_expected_names():
    from smriti_voice.config import LanguageRegistry
    registry = LanguageRegistry(Settings.load().language_pack_dir)
    packs = {p.code: p for p in registry.all()}
    assert packs['hin'].name == 'Hindi'
    assert packs['asm'].name == 'Assamese'
    assert 'mni' in packs and 'Meitei' in packs['mni'].name or 'Manipuri' in packs['mni'].name
    assert packs['kha'].name == 'Khasi'
    assert packs['lus'].name == 'Mizo'
    assert packs['eng'].name == 'English'


def test_khasi_and_mizo_have_no_configured_tts_provider():
    """Verified, not assumed: neither Sarvam Bulbul nor Indic Parler-TTS
    lists Khasi or Mizo in this codebase's configured language tables."""
    assert 'kha' not in SARVAM_TTS_LANGUAGES and 'kha' not in INDIC_PARLER_TTS_LANGUAGES
    assert 'lus' not in SARVAM_TTS_LANGUAGES and 'lus' not in INDIC_PARLER_TTS_LANGUAGES


def test_khasi_mizo_and_meiteilon_have_no_llm_conversation_capability():
    """A real, honest gap: none of the three are in LLM_LANGUAGES, so
    general conversation for them has no configured cloud LLM path in this
    codebase today -- only the deterministic command router (if the pack
    has phrases) and, for mni, local ASR."""
    assert 'kha' not in LLM_LANGUAGES
    assert 'lus' not in LLM_LANGUAGES
    assert 'mni' not in LLM_LANGUAGES


def test_meiteilon_has_validated_local_asr_pack_status():
    from smriti_voice.config import LanguageRegistry
    registry = LanguageRegistry(Settings.load().language_pack_dir)
    packs = {p.code: p for p in registry.all()}
    assert packs['mni'].status == 'validated_local'


def test_no_language_reports_supported_without_a_measured_validation_record():
    """Restates the existing, already-enforced rule (language/capabilities.py)
    for the three specific target languages this audit is about: adapter
    existence is never enough."""
    from smriti_voice.language.capabilities import build_capability, load_validation_records
    from smriti_voice.config import LanguageRegistry
    registry = LanguageRegistry(Settings.load().language_pack_dir)
    validation = load_validation_records()
    for code in ('hin', 'asm', 'mni', 'kha', 'lus', 'eng'):
        pack = registry.get(code)
        capability = build_capability(pack, validation)
        assert capability.status.value != 'SUPPORTED' or code in validation, (
            f'{code} reported SUPPORTED without a measured validation record')
