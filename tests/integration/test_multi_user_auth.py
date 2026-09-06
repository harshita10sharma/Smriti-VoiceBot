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
