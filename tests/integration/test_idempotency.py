"""Optional Idempotency-Key protection on POST /v1/conversation and
POST /v1/conversation/voice.

A client that sends nothing is completely unaffected (see the rest of the
suite, none of which sets this header). A client that opts in gets a real
guarantee: the same key with the same body executes exactly once, and the
same key with a different body is rejected rather than silently guessed at.
"""
from __future__ import annotations

import io
import wave

from test_conversation_scenarios import use_mock_llm


def _wav_bytes(seconds: float = 0.2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x00\x00' * int(16000 * seconds))
    return buf.getvalue()


def test_repeated_text_request_with_same_key_executes_once(app, client, auth_headers):
    provider = use_mock_llm(app, reply='Hello there.')
    body = {'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'}
    headers = {**auth_headers, 'x-idempotency-key': 'key-1'}

    first = client.post('/v1/conversation', json=body, headers=headers)
    second = client.post('/v1/conversation', json=body, headers=headers)

    assert first.status_code == second.status_code == 200
    assert first.json()['request_id'] == second.json()['request_id']
    assert len(provider.calls) == 1  # the model ran exactly once


def test_same_key_different_body_is_a_conflict(client, auth_headers):
    headers = {**auth_headers, 'x-idempotency-key': 'key-2'}
    first = client.post('/v1/conversation',
                        json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                        headers=headers)
    second = client.post('/v1/conversation',
                         json={'user_id': 'demo-user', 'message': 'goodbye', 'language': 'eng'},
                         headers=headers)
    assert first.status_code == 200
    assert second.status_code == 409


def test_no_idempotency_key_means_no_protection_at_all(app, client, auth_headers):
    """The default, unchanged behaviour for every existing client."""
    provider = use_mock_llm(app, reply='Hello there.')
    body = {'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'}
    client.post('/v1/conversation', json=body, headers=auth_headers)
    client.post('/v1/conversation', json=body, headers=auth_headers)
    assert len(provider.calls) == 2  # ran twice, exactly as before this feature existed


def test_voice_endpoint_idempotency_dedupes_on_exact_audio_bytes(app, client, auth_headers):
    from smriti_voice.schemas import ASRResult

    calls = {'n': 0}

    def stub_transcribe(*args, **kwargs):
        calls['n'] += 1
        return ASRResult(transcript='hello there', language='eng', provider='stub')

    app.asr.transcribe = stub_transcribe  # count and stub out real ASR entirely

    audio = _wav_bytes()
    headers = {**auth_headers, 'x-idempotency-key': 'voice-key-1'}
    files = {'audio_wav': ('a.wav', audio, 'audio/wav')}
    data = {'user_id': 'demo-user', 'language': 'eng', 'speak': 'false'}

    first = client.post('/v1/conversation/voice', headers=headers, files=files, data=data)
    files2 = {'audio_wav': ('a.wav', audio, 'audio/wav')}
    second = client.post('/v1/conversation/voice', headers=headers, files=files2, data=data)

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()['request_id'] == second.json()['request_id']
    assert calls['n'] == 1  # ASR (and therefore the whole pipeline) ran exactly once
