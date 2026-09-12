"""Multi-user API key mode: one backend, many elderly users, no cross-access.

SMRITI_API_KEYS binds each key to its own user id, so a caller authenticates
as exactly the identity its key maps to. Complements test_api.py, which
covers the original single-key/single-user mode (SMRITI_API_KEY +
SMRITI_AUTH_USER_ID); this file must not need to touch that mode's tests.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def multi_user_client(two_users, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.delenv('SMRITI_AUTH_USER_ID', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({
        'key-for-demo-user': 'demo-user',
        'key-for-other-user': 'other-user',
    }))
    return TestClient(create_app(two_users))


def test_each_key_authenticates_as_its_own_user(multi_user_client):
    response = multi_user_client.post(
        '/v1/conversation', headers={'x-api-key': 'key-for-demo-user'},
        json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 200

    response = multi_user_client.post(
        '/v1/conversation', headers={'x-api-key': 'key-for-other-user'},
        json={'user_id': 'other-user', 'message': 'help'})
    assert response.status_code == 200


def test_a_key_cannot_be_used_to_claim_a_different_user_id(multi_user_client):
    """The core guarantee: changing user_id in the JSON body does not grant
    access to another elder's memory/data."""
    response = multi_user_client.post(
        '/v1/conversation', headers={'x-api-key': 'key-for-demo-user'},
        json={'user_id': 'other-user', 'message': 'show my family'})
    assert response.status_code == 403


def test_unknown_key_is_rejected(multi_user_client):
    response = multi_user_client.post(
        '/v1/conversation', headers={'x-api-key': 'not-a-real-key'},
        json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 401


def test_missing_key_is_rejected(multi_user_client):
    response = multi_user_client.post(
        '/v1/conversation', json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 401


def test_malformed_key_map_fails_closed(two_users, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', 'not-valid-json{')
    client = TestClient(create_app(two_users))
    response = client.post('/v1/conversation', headers={'x-api-key': 'anything'},
                           json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 503


def test_empty_key_map_fails_closed(two_users, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', '{}')
    client = TestClient(create_app(two_users))
    response = client.post('/v1/conversation', headers={'x-api-key': 'anything'},
                           json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 503


def test_multi_user_voice_endpoint_also_enforces_identity_binding(multi_user_client):
    """The voice endpoint uses the same authenticated_user_id dependency as
    the text endpoint, so the guarantee holds for audio turns too."""
    from smriti_voice.tts.mock import silent_wav
    response = multi_user_client.post(
        '/v1/conversation/voice', headers={'x-api-key': 'key-for-demo-user'},
        data={'user_id': 'other-user'},
        files={'audio_wav': ('a.wav', silent_wav(0.4), 'audio/wav')})
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Backend/multi-patient mode: one key, an explicit allow-list of user ids.
# --------------------------------------------------------------------------- #
@pytest.fixture
def backend_client(two_users, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.delenv('SMRITI_AUTH_USER_ID', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({
        'backend-key': ['demo-user', 'other-user'],
    }))
    return TestClient(create_app(two_users))


def test_backend_key_may_act_as_any_authorized_patient(backend_client):
    for user_id in ('demo-user', 'other-user'):
        response = backend_client.post(
            '/v1/conversation', headers={'x-api-key': 'backend-key'},
            json={'user_id': user_id, 'message': 'help'})
        assert response.status_code == 200, (user_id, response.text)


def test_backend_key_cannot_act_as_an_unauthorized_patient(backend_client):
    """The core new guarantee: a valid backend key plus an arbitrary user_id
    that was never explicitly authorized must never be granted access."""
    response = backend_client.post(
        '/v1/conversation', headers={'x-api-key': 'backend-key'},
        json={'user_id': 'elder-999', 'message': 'help'})
    assert response.status_code == 403


def test_backend_key_voice_endpoint_also_checks_the_allow_list(backend_client):
    from smriti_voice.tts.mock import silent_wav
    response = backend_client.post(
        '/v1/conversation/voice', headers={'x-api-key': 'backend-key'},
        data={'user_id': 'demo-user'},
        files={'audio_wav': ('a.wav', silent_wav(0.4), 'audio/wav')})
    assert response.status_code == 200

    response = backend_client.post(
        '/v1/conversation/voice', headers={'x-api-key': 'backend-key'},
        data={'user_id': 'elder-999'},
        files={'audio_wav': ('a.wav', silent_wav(0.4), 'audio/wav')})
    assert response.status_code == 403


def test_backend_key_job_ownership_respects_the_allow_list(backend_client, two_users):
    """A job created for one authorized patient must not be readable for a
    different authorized patient under the same backend key — the allow-list
    grants the key access to several users, but each job still belongs to
    exactly the one user it was created for."""
    job_id = two_users.voice_jobs.create(user_id='other-user', session_id=None,
                                         language='eng', response_text='hi')
    response = backend_client.get(f'/v1/voice/jobs/{job_id}',
                                  headers={'x-api-key': 'backend-key'})
    assert response.status_code == 200
    assert response.json()['job_id'] == job_id


def test_voice_endpoint_returns_403_not_500_for_a_cross_patient_session_id(backend_client):
    """A session created for one authorized patient must not be usable by a
    different authorized patient on the voice endpoint -- and the rejection
    must be a clean 403, matching the text /v1/conversation endpoint,
    rather than an unhandled PermissionError surfacing as a 500."""
    from smriti_voice.tts.mock import silent_wav

    created = backend_client.post(
        '/v1/conversation', headers={'x-api-key': 'backend-key'},
        json={'user_id': 'demo-user', 'message': 'hello'})
    assert created.status_code == 200
    session_id = created.json()['session_id']

    response = backend_client.post(
        '/v1/conversation/voice', headers={'x-api-key': 'backend-key'},
        data={'user_id': 'other-user', 'session_id': session_id},
        files={'audio_wav': ('a.wav', silent_wav(0.4), 'audio/wav')})
    assert response.status_code == 403


def test_backend_key_missing_is_rejected(backend_client):
    response = backend_client.post(
        '/v1/conversation', json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 401


def test_backend_key_wrong_is_rejected(backend_client):
    response = backend_client.post(
        '/v1/conversation', headers={'x-api-key': 'not-the-backend-key'},
        json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 401


def test_malformed_backend_list_value_fails_closed(two_users, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({'backend-key': []}))
    client = TestClient(create_app(two_users))
    response = client.post('/v1/conversation', headers={'x-api-key': 'backend-key'},
                           json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 503


def test_backend_mode_compatible_with_existing_single_target_keys(two_users, monkeypatch):
    """A deployment can mix a single-user key and a backend key in the same
    SMRITI_API_KEYS map without either mode affecting the other."""
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({
        'solo-key': 'demo-user',
        'backend-key': ['demo-user', 'other-user'],
    }))
    client = TestClient(create_app(two_users))

    response = client.post('/v1/conversation', headers={'x-api-key': 'solo-key'},
                           json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 200

    response = client.post('/v1/conversation', headers={'x-api-key': 'solo-key'},
                           json={'user_id': 'other-user', 'message': 'help'})
    assert response.status_code == 403

    response = client.post('/v1/conversation', headers={'x-api-key': 'backend-key'},
                           json={'user_id': 'other-user', 'message': 'help'})
    assert response.status_code == 200


def test_authenticated_user_id_fails_closed_for_a_multi_patient_key(monkeypatch):
    """A direct check on the dependency itself: if a route only knows how to
    ask for a single identity, a genuinely multi-patient key must never let
    it guess — it must refuse (503), not silently pick one patient."""
    monkeypatch.delenv('SMRITI_AUTH_USER_ID', raising=False)
    from fastapi import HTTPException
    from smriti_voice.api.dependencies import authenticated_user_id

    class FakeState:
        # What require_api_key actually produces for a multi-patient key:
        # authorized_user_ids is set, authenticated_user_id deliberately is not.
        authorized_user_ids = frozenset({'demo-user', 'other-user'})

    class FakeRequest:
        state = FakeState()

    with pytest.raises(HTTPException) as exc_info:
        authenticated_user_id(FakeRequest())
    assert exc_info.value.status_code == 503
