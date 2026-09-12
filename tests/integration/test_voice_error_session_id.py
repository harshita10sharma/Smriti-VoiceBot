"""Real integration blocker found via direct external HTTP probing (not
caught by any prior unit/TestClient test): when a voice request omits
session_id and ASR fails or detects no speech, the response used to
return session_id="" -- an empty string -- unlike every other response
path (welcome, conversation, a successful voice turn), which always
returns a real, usable session_id. A client that reasonably assumes
"session_id in the response is always a valid identifier to continue the
conversation" would be given a useless empty string on exactly this one
error path.

Fixed to mint a real session id (the same generator every other path
uses) instead, so a client's next request naturally starts (or continues)
a real session regardless of which path produced this response.
"""
from __future__ import annotations

import io
import wave
from pathlib import Path

from smriti_voice.pipeline import VoicePipeline
from smriti_voice.schemas import TurnKind


def _silent_wav_path(tmp_path: Path) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x00\x00' * 8000)
    path = tmp_path / 'silent.wav'
    path.write_bytes(buf.getvalue())
    return path


def test_no_speech_detected_with_no_session_id_returns_a_real_session_id(app, tmp_path,
                                                                          monkeypatch):
    wav_path = _silent_wav_path(tmp_path)

    from smriti_voice.schemas import ASRResult

    def _empty_transcript(*args, **kwargs):
        return ASRResult(transcript='', provider='mock', latency_ms=1, offline=True,
                         language='eng', language_confidence=1.0)

    monkeypatch.setattr(app.asr, 'transcribe', _empty_transcript)
    result = VoicePipeline(app).process(wav_path, user_id='demo-user', session_id=None,
                                        language='eng')
    assert result.kind is TurnKind.ERROR
    assert result.audio_unavailable_reason == 'NO_SPEECH_DETECTED'
    assert result.session_id != ''
    assert len(result.session_id) > 0
    assert result.metadata.session_id == result.session_id  # both fields agree


def test_asr_unavailable_with_no_session_id_returns_a_real_session_id(app, tmp_path, monkeypatch):
    wav_path = _silent_wav_path(tmp_path)

    def _raise(*args, **kwargs):
        raise RuntimeError('simulated ASR provider failure')

    monkeypatch.setattr(app.asr, 'transcribe', _raise)
    result = VoicePipeline(app).process(wav_path, user_id='demo-user', session_id=None,
                                        language='eng')
    assert result.audio_unavailable_reason == 'ASR_UNAVAILABLE'
    assert result.session_id != ''


def test_error_path_reuses_the_callers_session_id_when_one_was_provided(app, tmp_path):
    """When the client sends back a session_id the server itself issued
    earlier (the only kind of session_id a contract-following client ever
    sends -- see INTEGRATION_CONTRACT.md, session_id is always server-
    generated), the error response must echo that same id back, never
    substitute a different one."""
    existing = app.conversation.sessions.get_or_create(None, 'demo-user', 'eng')
    wav_path = _silent_wav_path(tmp_path)
    result = VoicePipeline(app).process(wav_path, user_id='demo-user',
                                        session_id=existing.session_id, language='eng')
    assert result.session_id == existing.session_id


def test_a_session_id_minted_on_this_error_path_can_be_continued_normally(app, tmp_path):
    """The minted id isn't just cosmetic -- a subsequent real turn using it
    must work exactly like continuing any other session."""
    wav_path = _silent_wav_path(tmp_path)
    error_result = VoicePipeline(app).process(wav_path, user_id='demo-user', session_id=None,
                                               language='eng')
    minted_session_id = error_result.session_id

    from test_conversation_scenarios import use_mock_llm
    use_mock_llm(app, reply='Continuing the same session works.')
    follow_up = app.conversation.handle(user_id='demo-user', message='hello',
                                        session_id=minted_session_id, language='eng')
    assert follow_up.session_id == minted_session_id


def test_voice_endpoint_over_http_never_returns_an_empty_session_id(client, auth_headers, app,
                                                                     tmp_path):
    """The same guarantee, exercised through the actual HTTP route rather
    than the pipeline object directly."""
    wav_path = _silent_wav_path(tmp_path)
    resp = client.post('/v1/conversation/voice', headers=auth_headers,
                       data={'user_id': 'demo-user', 'language': 'eng', 'speak': 'true'},
                       files={'audio_wav': ('a.wav', wav_path.read_bytes(), 'audio/wav')})
    assert resp.status_code == 200
    body = resp.json()
    assert body['session_id'] != ''
