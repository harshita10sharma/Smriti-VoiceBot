"""Regression coverage for a real, reproduced defect: the default Sarvam
Bulbul model/speaker pairing shipped as 'bulbul:v2' + 'anushka', both of
which the live Sarvam API now rejects -- 'bulbul:v2' is deprecated, and
'anushka' is not among the speakers 'bulbul:v3' accepts. Every real
synthesis call therefore failed with TTS_UNAVAILABLE regardless of
credentials or network access; this was root-caused against the live API
(a direct HTTP call, not a guess) before being fixed.

These tests never call the real network -- they pin the *configuration*
that a live call depends on, so a future accidental revert to the
deprecated model/speaker pairing is caught here, cheaply, without needing
network access or live credentials for every test run.
"""
from __future__ import annotations

from smriti_voice.config import ProviderConfig
from smriti_voice.language.capabilities import SARVAM_TTS_LANGUAGES, SARVAM_TTS_VOICES
from smriti_voice.tts.sarvam import SarvamTTSProvider

# The exact speaker set the live Sarvam API reported as valid for
# 'bulbul:v3' when this defect was diagnosed (see STAGING_READINESS.md).
# Not exhaustive of what Sarvam might add later, but any speaker actually
# configured here must currently be a member of it.
KNOWN_VALID_BULBUL_V3_SPEAKERS = frozenset({
    'aditya', 'ritu', 'ashutosh', 'priya', 'neha', 'rahul', 'pooja', 'rohan',
    'simran', 'kavya', 'amit', 'dev', 'ishita', 'shreya', 'ratan', 'varun',
    'manan', 'sumit', 'roopa', 'kabir', 'aayan', 'shubh', 'advait', 'anand',
    'tanya', 'tarun', 'sunny', 'mani', 'gokul', 'vijay', 'shruti', 'suhani',
    'mohit', 'kavitha', 'rehan', 'soham', 'rupali',
})

# 'bulbul:v2' is deprecated by Sarvam; do not default back to it.
DEPRECATED_MODEL = 'bulbul:v2'


def test_default_provider_config_does_not_use_the_deprecated_bulbul_v2_model():
    assert ProviderConfig().tts_model != DEPRECATED_MODEL
    assert ProviderConfig().tts_model == 'bulbul:v3'


def test_sarvam_provider_constructor_default_does_not_use_the_deprecated_model():
    provider = SarvamTTSProvider(api_key='irrelevant-for-this-check')
    assert provider.model != DEPRECATED_MODEL


def test_every_configured_sarvam_voice_is_a_known_valid_bulbul_v3_speaker():
    """Every language SARVAM_TTS_VOICES actually configures a speaker for
    must currently be one bulbul:v3 accepts -- an unlisted or v2-only
    speaker (like the old default 'anushka') fails every real call for
    that language, exactly as it did for every language before this fix,
    since every language shared the same single default speaker."""
    assert set(SARVAM_TTS_VOICES) == set(SARVAM_TTS_LANGUAGES)
    for language, speaker in SARVAM_TTS_VOICES.items():
        assert speaker in KNOWN_VALID_BULBUL_V3_SPEAKERS, (
            f'{language!r} is configured with speaker {speaker!r}, which is not a '
            f'known-valid bulbul:v3 speaker -- every real synthesis call for this '
            f'language would fail')


def test_sarvam_provider_sends_the_configured_model_and_a_valid_speaker(monkeypatch):
    """A mocked-network unit test: confirms the provider actually places
    the configured (non-deprecated) model and a known-valid speaker into
    the request body it would send -- not a live network assertion."""
    captured = {}

    class _FakeResponse:
        status_code = 200

        def json(self):
            import base64
            return {'audios': [base64.b64encode(b'RIFF....WAVEfmt ').decode()]}

    def _fake_post(url, *, headers, json, timeout):
        captured['body'] = json
        return _FakeResponse()

    monkeypatch.setattr('smriti_voice.tts.sarvam.httpx.post', _fake_post)

    provider = SarvamTTSProvider(api_key='fake-key')
    provider.synthesize('Hello there', 'eng')

    assert captured['body']['model'] != DEPRECATED_MODEL
    assert captured['body']['speaker'] in KNOWN_VALID_BULBUL_V3_SPEAKERS
