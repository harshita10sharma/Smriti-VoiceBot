"""The voice endpoint end to end.

Only the network ASR call is stubbed — everything downstream (detection, safety,
the turn router, tools, TTS routing, the audio store and the HTTP layer) is real.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from smriti_voice.api.app import create_app
from smriti_voice.schemas import ASRResult, TurnKind
from smriti_voice.tts.mock import silent_wav

WAV = silent_wav(0.5)


class StubASR:
    """Returns a fixed transcript. Records what it was asked for."""

    def __init__(self, transcript='open play', language='en-IN', confidence=0.97):
        self.transcript = transcript
        self.language = language
        self.confidence = confidence
        self.calls: list[str] = []

    def transcribe(self, wav, language, request_id=''):
        self.calls.append(language)
        return ASRResult(transcript=self.transcript, language=self.language,
                         language_confidence=self.confidence, provider='stub',
                         offline=False, latency_ms=42)


class FailingASR:
    def transcribe(self, wav, language, request_id=''):
        raise RuntimeError('no ASR provider reachable')


@pytest.fixture
def voice_client(app, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEY', 'test-server-key')
    monkeypatch.setenv('SMRITI_AUTH_USER_ID', 'demo-user')
    app.asr = StubASR()
    return TestClient(create_app(app)), app


def post_voice(client, headers, **data):
    return client.post('/v1/conversation/voice', headers=headers,
                       data={'user_id': 'demo-user', **data},
                       files={'audio_wav': ('u.wav', WAV, 'audio/wav')})


def test_voice_turn_returns_transcript_action_and_audio(voice_client, auth_headers):
    client, app = voice_client
    response = post_voice(client, auth_headers, language='eng')
    assert response.status_code == 200
    body = response.json()

    assert body['transcript'] == 'open play'
    assert body['kind'] == TurnKind.COMMAND.value
    assert body['action'] == 'OPEN_PLAY' and body['action_accepted'] is True
    assert body['audio_available'] is True
    assert body['audio_id'] and body['audio_url'] == f"/v1/audio/{body['audio_id']}"
    assert body['metadata']['asr_provider'] == 'stub'
    assert body['metadata']['asr_latency_ms'] == 42
    assert body['metadata']['total_latency_ms'] >= 0
    # Audio bytes must never appear in the JSON body.
    assert 'audio' not in body


def test_generated_audio_is_fetchable_once_and_is_a_real_wav(voice_client, auth_headers):
    client, _ = voice_client
    body = post_voice(client, auth_headers, language='eng').json()
    audio = client.get(body['audio_url'], headers=auth_headers)
    assert audio.status_code == 200
    assert audio.headers['content-type'] == 'audio/wav'
    assert audio.content[:4] == b'RIFF' and audio.content[8:12] == b'WAVE'


def test_speak_false_returns_text_only(voice_client, auth_headers):
    client, _ = voice_client
    body = post_voice(client, auth_headers, language='eng', speak='false').json()
    assert body['audio_available'] is False
    assert body['audio_unavailable_reason'] == 'TTS_NOT_REQUESTED'
    assert body['response_text']


def test_language_is_detected_when_not_supplied(voice_client, auth_headers):
    client, app = voice_client
    app.asr = StubASR(transcript='आप कैसे हैं', language=None, confidence=0.0)
    body = post_voice(client, auth_headers).json()
    assert body['language'] == 'hin'


def test_asr_failure_degrades_gracefully(voice_client, auth_headers):
    client, app = voice_client
    app.asr = FailingASR()
    response = post_voice(client, auth_headers, language='eng')
    assert response.status_code == 200          # a spoken error, not an HTTP error
    body = response.json()
    assert body['kind'] == TurnKind.ERROR.value
    assert body['metadata']['error_code'] == 'ASR_UNAVAILABLE'
    assert body['audio_available'] is False
    assert 'microphone' in body['response_text'].lower()


def test_empty_transcript_is_reported_as_no_speech(voice_client, auth_headers):
    client, app = voice_client
    app.asr = StubASR(transcript='   ')
    body = post_voice(client, auth_headers, language='eng').json()
    assert body['metadata']['error_code'] == 'NO_SPEECH_DETECTED'


def test_asr_error_message_is_in_the_users_language(voice_client, auth_headers):
    client, app = voice_client
    app.asr = FailingASR()
    body = post_voice(client, auth_headers, language='hin').json()
    assert any(ord(character) > 0x900 for character in body['response_text'])


def test_unsafe_speech_is_refused_through_the_voice_path(voice_client, auth_headers):
    client, app = voice_client
    app.asr = StubASR(transcript='delete my medicine')
    body = post_voice(client, auth_headers, language='eng').json()
    assert body['kind'] == TurnKind.REFUSAL.value
    assert body['action'] == 'NO_ACTION' and body['action_accepted'] is False


def test_assamese_returns_text_but_reports_no_voice(voice_client, auth_headers, monkeypatch):
    """The whole point of the Assamese gap, exercised over HTTP."""
    client, app = voice_client
    monkeypatch.setenv('SMRITI_TTS_PROVIDER', 'auto')
    monkeypatch.setenv('SARVAM_API_KEY', 'present-but-never-called')
    from smriti_voice.config import AppConfig
    from smriti_voice.tts.router import TTSRouter
    app.tts = TTSRouter(AppConfig.load(), app.languages)
    app.asr = StubASR(transcript='delete my medicine', language='as-IN')

    body = post_voice(client, auth_headers, language='asm').json()
    assert body['language'] == 'asm'
    assert body['response_text']                       # text is still produced
    assert body['audio_available'] is False
    assert body['audio_unavailable_reason'] == 'NO_TTS_PROVIDER_SUPPORTS_LANGUAGE'


def test_temporary_upload_file_is_removed(voice_client, auth_headers, tmp_path):
    import tempfile
    client, _ = voice_client
    before = set(pathlib_iterdir(tempfile.gettempdir()))
    post_voice(client, auth_headers, language='eng')
    after = set(pathlib_iterdir(tempfile.gettempdir()))
    leaked = [name for name in after - before if name.endswith('.wav')]
    assert not leaked, f'temporary upload files leaked: {leaked}'


def pathlib_iterdir(directory):
    import os
    try:
        return os.listdir(directory)
    except OSError:
        return []
