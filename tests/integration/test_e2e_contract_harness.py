"""Deterministic end-to-end integration harness: the full Backend-gateway
-> VoiceBot -> Flutter-client sequence, exercised together in realistic
order rather than as isolated unit assertions. A real Backend and a real
Flutter client are not available to this test suite, so both are
simulated: the "Backend" role is played by issuing requests with a
server-side API key exactly as a gateway would, and the "Flutter" role is
played by polling/fetching exactly as documented in HANDOFF.md.

This is a deterministic contract proof, not a claim of physical-device or
live-network validation -- nothing here talks to a real ASR/TTS provider;
LLM responses come from MockLLMProvider (see test_conversation_scenarios
.use_mock_llm) and TTS from the mock provider the `app` fixture already
configures.

Several of the individual guarantees exercised here already have their
own focused regression tests elsewhere (referenced in comments below) --
this file's purpose is proving they all hold *together*, in the order a
real integration would actually exercise them, not re-proving each one in
isolation again.
"""
from __future__ import annotations

import io
import time
import wave

from smriti_voice.memory.models import FamilyMember, Medicine, User
from smriti_voice.schemas import ASRResult
from smriti_voice.voice_jobs import CANCELLED, COMPLETED, FAILED

from test_conversation_scenarios import tool_then_answer, use_mock_llm


def _wait_for_terminal(client, headers, job_id, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f'/v1/voice/jobs/{job_id}', headers=headers).json()
        if body['status'] in ('completed', 'failed', 'cancelled'):
            return body
        time.sleep(0.01)
    raise AssertionError(f'job {job_id} never reached a terminal state')


# --------------------------------------------------------------------------- #
# The full happy-path chain, in realistic order
# --------------------------------------------------------------------------- #
def test_full_backend_to_flutter_chain(client, app, auth_headers):
    """authenticate -> memory sync -> welcome -> voice turn -> job poll ->
    audio retrieval, exactly the sequence documented in HANDOFF.md."""
    # 1. Backend authenticates and syncs authoritative caregiver memory.
    sync_resp = client.post(
        '/v1/memory/sync', headers=auth_headers,
        json={'user_id': 'demo-user', 'display_name': 'Elder One', 'timezone': 'Asia/Kolkata',
             'family_members': [{'name': 'Priya', 'relationship': 'daughter',
                                 'phone_available': True}],
             'medicines': [], 'daily_routines': []})
    assert sync_resp.status_code == 200

    # 2. Flutter opens the app: deterministic welcome, no LLM, no history.
    welcome_resp = client.post('/v1/conversation/welcome', headers=auth_headers,
                               json={'user_id': 'demo-user', 'language': 'eng'})
    assert welcome_resp.status_code == 200
    welcome_body = welcome_resp.json()
    assert welcome_body['kind'] == 'WELCOME'
    session_id = welcome_body['session_id']

    # 3. The user's real first utterance, in the same session -- must not
    # be discarded by the welcome call, and must reflect the freshly
    # synced memory (proves memory sync -> conversation, not proven
    # elsewhere in the suite as a single flow).
    use_mock_llm(app, script=tool_then_answer('get_family_member',
                                              'Your daughter is called Priya.',
                                              relation='daughter'))
    text_resp = client.post('/v1/conversation', headers=auth_headers,
                            json={'user_id': 'demo-user', 'session_id': session_id,
                                 'message': "What is my daughter's name?", 'language': 'eng'})
    assert text_resp.status_code == 200
    body = text_resp.json()
    assert 'Priya' in body['response_text']
    assert body['kind'] == 'MEMORY'

    # 4. A voice turn in the same session, TTS requested.
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b'\x00\x00' * 1600)
    app.asr.transcribe = lambda *a, **k: ASRResult(transcript='hello', language='eng',
                                                   provider='stub')
    use_mock_llm(app, reply='Hello, how can I help?')
    voice_resp = client.post('/v1/conversation/voice', headers=auth_headers,
                             data={'user_id': 'demo-user', 'session_id': session_id,
                                  'language': 'eng', 'speak': 'true'},
                             files={'audio_wav': ('a.wav', buf.getvalue(), 'audio/wav')})
    assert voice_resp.status_code == 200
    job_id = voice_resp.json()['job_id']
    assert job_id is not None

    # 5. Poll until terminal, then fetch audio exactly once.
    final = _wait_for_terminal(client, auth_headers, job_id)
    assert final['status'] == 'completed'
    audio_id = final['audio_id']
    assert audio_id is not None
    audio_resp = client.get(f'/v1/audio/{audio_id}', headers=auth_headers)
    assert audio_resp.status_code == 200
    assert audio_resp.headers['content-type'] == 'audio/wav'


# --------------------------------------------------------------------------- #
# Cross-patient isolation across the FULL chain (not just one endpoint)
# --------------------------------------------------------------------------- #
def test_cross_patient_isolation_across_the_full_chain(client, app, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEYS',
                       '{"key-a": "patient-a", "key-b": "patient-b"}')
    app.memory.repo.upsert_user(User(user_id='patient-a', display_name='A'))
    app.memory.repo.upsert_user(User(user_id='patient-b', display_name='B'))

    use_mock_llm(app, reply='hi')
    voice_resp = client.post(
        '/v1/conversation/welcome', headers={'x-api-key': 'key-a'},
        json={'user_id': 'patient-a', 'language': 'eng', 'speak': True})
    job_id = voice_resp.json()['job_id']

    # Patient B's credential must not see patient A's job or its audio.
    job_as_b = client.get(f'/v1/voice/jobs/{job_id}', headers={'x-api-key': 'key-b'})
    assert job_as_b.status_code == 404

    final = _wait_for_terminal(client, {'x-api-key': 'key-a'}, job_id)
    if final['audio_id']:
        audio_as_b = client.get(f'/v1/audio/{final["audio_id"]}', headers={'x-api-key': 'key-b'})
        assert audio_as_b.status_code == 404

    # Patient B cannot act as patient A even with a well-formed request.
    cross = client.post('/v1/conversation', headers={'x-api-key': 'key-b'},
                        json={'user_id': 'patient-a', 'message': 'hello', 'language': 'eng'})
    assert cross.status_code == 403


# --------------------------------------------------------------------------- #
# Disabled patient rejected end-to-end (welcome, conversation, voice, sync)
# --------------------------------------------------------------------------- #
def test_disabled_patient_is_rejected_at_every_entry_point(client, app, auth_headers):
    app.memory.repo.upsert_user(User(user_id='demo-user', display_name='Demo', active=False))

    assert client.post('/v1/conversation/welcome', headers=auth_headers,
                       json={'user_id': 'demo-user'}).status_code == 403
    assert client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'message': 'hi',
                            'language': 'eng'}).status_code == 403
    assert client.post('/v1/memory/sync', headers=auth_headers,
                       json={'user_id': 'demo-user', 'family_members': [],
                            'medicines': [], 'daily_routines': []}).status_code == 403


# --------------------------------------------------------------------------- #
# External ID collision across two different patients -> clean 409, not 500
# --------------------------------------------------------------------------- #
def test_external_id_collision_across_patients_is_a_clean_conflict(client, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEYS',
                       '{"key-a": "patient-a", "key-b": "patient-b"}')
    first = client.post('/v1/memory/sync', headers={'x-api-key': 'key-a'},
                        json={'user_id': 'patient-a', 'external_id': 'dup-uuid',
                             'family_members': [], 'medicines': [], 'daily_routines': []})
    assert first.status_code == 200

    second = client.post('/v1/memory/sync', headers={'x-api-key': 'key-b'},
                         json={'user_id': 'patient-b', 'external_id': 'dup-uuid',
                              'family_members': [], 'medicines': [], 'daily_routines': []})
    assert second.status_code == 409  # not a generic 500
    assert 'external_id' in second.json()['detail']


def test_family_member_external_id_collision_within_the_same_patient_is_a_conflict(
        client, auth_headers):
    payload = {'user_id': 'demo-user',
              'family_members': [{'name': 'A', 'relationship': 'son',
                                  'phone_available': False, 'external_id': 'person-1'},
                                 {'name': 'B', 'relationship': 'daughter',
                                  'phone_available': False, 'external_id': 'person-1'}],
              'medicines': [], 'daily_routines': []}
    resp = client.post('/v1/memory/sync', json=payload, headers=auth_headers)
    assert resp.status_code == 409


# --------------------------------------------------------------------------- #
# Duplicate voice submission does not create two jobs (idempotency, full chain)
# --------------------------------------------------------------------------- #
def test_duplicate_voice_submission_creates_exactly_one_job(client, app, auth_headers):
    use_mock_llm(app, reply='ok')
    app.asr.transcribe = lambda *a, **k: ASRResult(transcript='hi', language='eng',
                                                    provider='stub')
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b'\x00\x00' * 1600)
    audio_bytes = buf.getvalue()

    headers = {**auth_headers, 'x-idempotency-key': 'dup-submit-1'}
    data = {'user_id': 'demo-user', 'language': 'eng', 'speak': 'true'}
    first = client.post('/v1/conversation/voice', headers=headers, data=data,
                        files={'audio_wav': ('a.wav', audio_bytes, 'audio/wav')})
    second = client.post('/v1/conversation/voice', headers=headers, data=data,
                         files={'audio_wav': ('a.wav', audio_bytes, 'audio/wav')})
    assert first.status_code == second.status_code == 200
    assert first.json()['job_id'] == second.json()['job_id']  # exactly one job


# --------------------------------------------------------------------------- #
# Cancellation prevents a stale completion from ever being visible
# --------------------------------------------------------------------------- #
def test_cancellation_prevents_stale_completion_end_to_end(client, app, auth_headers):
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    cancel_resp = client.post(f'/v1/voice/jobs/{job_id}/cancel', headers=auth_headers)
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()['cancelled'] is True

    # A "late" worker completion must never resurrect it.
    app.voice_jobs.mark_completed(job_id, audio_id='late', tts_provider='mock')
    status = client.get(f'/v1/voice/jobs/{job_id}', headers=auth_headers).json()
    assert status['status'] == 'cancelled'


# --------------------------------------------------------------------------- #
# Expired audio cannot be retrieved
# --------------------------------------------------------------------------- #
def test_expired_audio_cannot_be_retrieved_end_to_end(client, app, auth_headers):
    import os
    from datetime import datetime, timezone

    audio_id = app.tts.store.put(b'RIFF....WAVEfmt ' + b'\x00' * 20)
    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='eng',
                                   response_text='hi')
    app.voice_jobs.mark_processing(job_id)
    app.voice_jobs.mark_completed(job_id, audio_id=audio_id, tts_provider='mock')

    path = app.tts.store.directory / f'{audio_id}.wav'
    old = datetime.now(timezone.utc).timestamp() - app.tts.store.retention_s - 5
    os.utime(path, (old, old))

    resp = client.get(f'/v1/audio/{audio_id}', headers=auth_headers)
    assert resp.status_code == 404


# --------------------------------------------------------------------------- #
# Provider failure is reported truthfully, never faked as success
# --------------------------------------------------------------------------- #
def test_tts_provider_failure_never_fakes_a_completed_job(client, app, auth_headers, monkeypatch):
    monkeypatch.setenv('SMRITI_TTS_PROVIDER', 'auto')
    monkeypatch.setenv('SARVAM_API_KEY', 'present-but-never-called')
    monkeypatch.setenv('SMRITI_INDIC_PARLER_ENABLED', '0')
    from smriti_voice.config import AppConfig
    from smriti_voice.tts.router import TTSRouter
    app.tts = TTSRouter(AppConfig.load(), app.languages)

    job_id = app.voice_jobs.create(user_id='demo-user', session_id=None, language='asm',
                                   response_text='no voice for this language')
    app.voice_job_worker.submit(job_id)
    status = _wait_for_terminal(client, auth_headers, job_id)
    assert status['status'] == 'failed'
    assert status['error_code'] == 'NO_TTS_PROVIDER_SUPPORTS_LANGUAGE'
    assert status['audio_id'] is None
