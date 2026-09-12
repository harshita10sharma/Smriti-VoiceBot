"""Per-language capability facts, and the rules that turn them into a status.

The hard rule of this module: **a language is never reported as SUPPORTED
because a vendor's documentation lists it.**  Vendor coverage only ever produces
a *capability* (``asr_online=True``).  The ``status`` is downgraded to
``NOT_YET_TESTED`` unless ``config/language_validation.json`` carries a record
produced by an actual measured test run.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..schemas import LanguageCapability, LanguageStatus

ROOT = Path(__file__).resolve().parents[2]
VALIDATION_PATH = ROOT / 'config' / 'language_validation.json'

# --------------------------------------------------------------------------- #
# Vendor capability facts.  Sources are cited in LANGUAGE_SUPPORT.md.
# --------------------------------------------------------------------------- #

# Sarvam Saaras speech-to-text: 22 Indic languages + English.
# This mirrors SarvamASR.SUPPORTED, which is the code that actually sends the code.
SARVAM_ASR_LANGUAGES: dict[str, str] = {
    'eng': 'en-IN', 'hin': 'hi-IN', 'asm': 'as-IN', 'ben': 'bn-IN', 'brx': 'brx-IN',
    'mni': 'mni-IN', 'npi': 'ne-IN', 'kok': 'kok-IN', 'ksm': 'ks-IN', 'snd': 'sd-IN',
    'san': 'sa-IN', 'sat': 'sat-IN', 'doi': 'doi-IN', 'guj': 'gu-IN', 'mar': 'mr-IN',
    'ori': 'od-IN', 'pan': 'pa-IN', 'tam': 'ta-IN', 'tel': 'te-IN', 'kan': 'kn-IN',
    'mal': 'ml-IN', 'urd': 'ur-IN',
}

# Sarvam Bulbul text-to-speech.  This list is SHORTER than the ASR list:
# Bulbul documents 10 Indian languages + English.  Assamese, Bodo, Manipuri,
# Nepali and every Northeast language below are NOT covered, so those languages
# get ASR and reasoning but no cloud voice output.
SARVAM_TTS_LANGUAGES: dict[str, str] = {
    'eng': 'en-IN', 'hin': 'hi-IN', 'ben': 'bn-IN', 'kan': 'kn-IN', 'mal': 'ml-IN',
    'mar': 'mr-IN', 'ori': 'od-IN', 'pan': 'pa-IN', 'tam': 'ta-IN', 'tel': 'te-IN',
    'guj': 'gu-IN',
}

# Indic Parler-TTS languages.  Supports 22 Indic languages + English.
# See: https://huggingface.co/ai4bharat/indic-parler-tts
INDIC_PARLER_TTS_LANGUAGES: dict[str, str] = {
    'asm': 'asm',  # Assamese
    'ben': 'ben',  # Bengali
    'brx': 'brx',  # Bodo
    'eng': 'eng',  # English
    'guj': 'guj',  # Gujarati
    'hin': 'hin',  # Hindi
    'kan': 'kan',  # Kannada
    'mai': 'mai',  # Maithili
    'mal': 'mal',  # Malayalam
    'mni': 'mni',  # Manipuri
    'mar': 'mar',  # Marathi
    'npi': 'npi',  # Nepali
    'ori': 'ori',  # Odia
    'pan': 'pan',  # Punjabi
    'san': 'san',  # Sanskrit
    'sat': 'sat',  # Santali
    'snd': 'snd',  # Sindhi
    'tam': 'tam',  # Tamil
    'tel': 'tel',  # Telugu
    'urd': 'urd',  # Urdu
}

# Bulbul v2 voices.  A single neutral, calm voice is used for every language;
# a per-language override belongs in configs once native speakers have chosen one.
SARVAM_TTS_VOICES: dict[str, str] = {code: 'anushka' for code in SARVAM_TTS_LANGUAGES}

# Languages the general-purpose cloud LLMs handle well enough for an elderly
# assistant.  Deliberately conservative: the Northeast low-resource languages are
# excluded because their generation quality has not been measured here.
LLM_LANGUAGES: frozenset[str] = frozenset({
    'eng', 'hin', 'ben', 'asm', 'guj', 'mar', 'tam', 'tel', 'kan', 'mal', 'ori',
    'pan', 'urd', 'npi', 'san',
})

# NE-LID covers 11 Northeast languages; Sarvam auto-detects its own 23.
NE_LID_LANGUAGES: frozenset[str] = frozenset({
    'asm', 'ben', 'brx', 'mni', 'npi', 'kha', 'lus', 'grt', 'trp', 'nag', 'nyish',
})

# Human-readable limitations attached to the matrix so nobody has to guess.
STATIC_LIMITATIONS: dict[str, list[str]] = {
    'asm': ['Sarvam Bulbul TTS does not list Assamese: cloud voice output is unavailable.',
            'Assamese and Bengali share a script, so offline script detection cannot separate '
            'them unless the text contains an Assamese-only letter. Detection relies on NE-LID '
            'or the provider hint.'],
    'brx': ['No documented cloud TTS.', 'LLM generation quality not measured.'],
    'mni': ['No documented cloud TTS.', 'LLM generation quality not measured.',
            'Command pack ships with no phrases, so the command router fails closed.'],
    'npi': ['No documented cloud TTS.'],
    # The 'LLM generation quality not measured' line matters precisely:
    # LLM_LANGUAGES (the llm_support flag above) is a reporting-only
    # allowlist -- nothing in the conversation pipeline actually refuses a
    # turn in this language (the system prompt names it correctly; see
    # tests/unit/test_target_language_mappings.py). A user CAN talk to the
    # assistant in this language today; the honest gap is that nobody has
    # measured whether the model's output is any good, not that no path
    # exists -- this line exists so a caller reading known_limitations
    # gets that nuance rather than assuming a hard refusal.
    'kha': ['No cloud ASR or TTS. Local NE-ASR model is benchmark-only.',
           'LLM generation quality not measured.'],
    'lus': ['No cloud ASR or TTS. Local NE-ASR model is benchmark-only.',
           'LLM generation quality not measured.'],
    'grt': ['No cloud ASR or TTS. Local NE-ASR model is benchmark-only.'],
    'trp': ['No cloud ASR or TTS. Local NE-ASR model is benchmark-only.'],
    'nag': ['No cloud ASR or TTS. Local NE-ASR model is benchmark-only.'],
    'ccp': ['No cloud ASR or TTS. Local NE-ASR model is benchmark-only.'],
    'wao': ['No cloud ASR or TTS. Local NE-ASR model is benchmark-only.'],
    'nyish': ['No ASR of any kind is shipped. Voice input is unavailable; use the touch fallback.'],
}


def load_validation_records(path: Path | None = None) -> dict[str, dict]:
    """Measured-evidence records, keyed by language code.  Missing file → no claims."""
    p = Path(path or VALIDATION_PATH)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text(encoding='utf-8'))
    except json.JSONDecodeError:
        return {}
    return {rec['code']: rec for rec in raw.get('validated', []) if isinstance(rec, dict) and 'code' in rec}


def _status_for(*, asr_online: bool, asr_offline: bool, tts: bool, llm: bool,
                pack_status: str, validated: bool) -> LanguageStatus:
    """Derive a status.  Nothing reaches SUPPORTED without measured evidence.

    ``asr_offline`` means *runtime-enabled* local ASR, not merely "a model id is
    written down".  A pack marked ``benchmark_only`` has a model that the engine
    deliberately refuses to load in production, so it reports BENCHMARK_ONLY —
    which is a real, if limited, position, not UNSUPPORTED.
    """
    runtime_asr = asr_online or asr_offline
    if not runtime_asr:
        if pack_status == 'benchmark_only':
            return LanguageStatus.BENCHMARK_ONLY
        return LanguageStatus.UNSUPPORTED
    if not validated:
        return LanguageStatus.NOT_YET_TESTED
    if asr_online and not asr_offline:
        return LanguageStatus.ONLINE_ONLY
    if asr_offline and not asr_online:
        return LanguageStatus.OFFLINE_ONLY
    if not (tts and llm):
        return LanguageStatus.SUPPORTED_WITH_LIMITATIONS
    return LanguageStatus.SUPPORTED


def build_capability(pack, validation: dict[str, dict] | None = None) -> LanguageCapability:
    """Build one matrix row from a v4.1 :class:`LanguagePack` plus vendor facts."""
    validation = validation or {}
    code = pack.code
    record = validation.get(code)

    asr_online = code in SARVAM_ASR_LANGUAGES
    local_providers = [p for p in pack.providers if p not in {'sarvam', 'openai'}]
    asr_offline = bool(local_providers) and pack.status == 'validated_local'

    # The matrix reports providers configured for the application, not merely
    # model-card coverage. Indic Parler is optional and selected by the router
    # only when explicitly enabled, so it must not make a language appear
    # speakable by default.
    tts_online = code in SARVAM_TTS_LANGUAGES
    llm = code in LLM_LANGUAGES

    limitations = list(STATIC_LIMITATIONS.get(code, []))
    if asr_online and not tts_online:
        note = 'Speech input works online, but no configured provider can speak this language.'
        if note not in limitations:
            limitations.append(note)
    if pack.status == 'benchmark_only':
        limitations.append('Local model is benchmark-only: not validated on device with native speakers.')
    if pack.status == 'unsupported_local':
        limitations.append('No local ASR is shipped for this language.')
    if not record:
        limitations.append('No measured end-to-end validation record exists for this language.')

    return LanguageCapability(
        code=code,
        name=pack.name,
        script=pack.script,
        asr_provider_online='sarvam' if asr_online else None,
        asr_model_online='saaras:v4' if asr_online else None,
        asr_provider_offline=local_providers[0] if local_providers else None,
        asr_model_offline=pack.model_id if local_providers else None,
        asr_online=asr_online,
        asr_offline=asr_offline,
        llm_support=llm,
        tts_provider='sarvam' if tts_online else None,
        tts_online=tts_online,
        tts_offline=False,  # No local TTS model is shipped. See OFFLINE.md.
        tts_voice=SARVAM_TTS_VOICES.get(code),
        language_detection=(code in NE_LID_LANGUAGES) or asr_online,
        status=_status_for(asr_online=asr_online, asr_offline=asr_offline, tts=tts_online,
                           llm=llm, pack_status=pack.status, validated=bool(record)),
        benchmark_status=(record.get('benchmark_status', 'validated') if record else 'not_benchmarked'),
        validated=bool(record),
        known_limitations=limitations,
    )
