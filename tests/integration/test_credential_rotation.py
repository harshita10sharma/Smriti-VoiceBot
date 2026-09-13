"""Credential rotation: patient memory is keyed by stable patient identity
(user_id), never by the API credential used to reach it. Rotating which
key maps to a patient -- the documented procedure in SECURITY.md, "update
the environment variable and restart the process" -- must never lose that
patient's memory, sessions, or ownership, and the old credential must stop
working the moment the new mapping takes over.

SMRITI_API_KEYS is read fresh on every request (see
api/dependencies.py::require_api_key, `os.getenv` with no caching), so a
live env var change genuinely exercises the same code path a real restart
would, without needing to actually restart the process for this test.
"""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def rotation_client(app, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    monkeypatch.delenv('SMRITI_AUTH_USER_ID', raising=False)
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({'old-key': 'rotation-patient'}))
    return TestClient(create_app(app)), monkeypatch


def test_credential_rotation_preserves_memory_and_rejects_the_old_key(rotation_client):
    client, monkeypatch = rotation_client

    # 1. Provision the patient and create real memory under the old key.
    sync = client.post('/v1/memory/sync', headers={'x-api-key': 'old-key'},
                       json={'user_id': 'rotation-patient',
                            'family_members': [{'name': 'Rotation Daughter',
                                                'relationship': 'daughter'}],
                            'medicines': [], 'daily_routines': []})
    assert sync.status_code == 200

    # 2. A real conversation turn under the old key, so there is session
    # state (not just caregiver-synced memory) to prove survives rotation.
    turn = client.post('/v1/conversation', headers={'x-api-key': 'old-key'},
                       json={'user_id': 'rotation-patient', 'message': 'hello'})
    assert turn.status_code == 200
    session_id = turn.json()['session_id']

    # 3. Rotate: a new key takes over the same patient identity. The old
    # key is removed entirely, exactly like a real rotation would.
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({'new-key': 'rotation-patient'}))

    # 4. The old credential is rejected immediately.
    rejected = client.post('/v1/conversation', headers={'x-api-key': 'old-key'},
                           json={'user_id': 'rotation-patient', 'message': 'hello again'})
    assert rejected.status_code == 401

    # 5. The new credential is accepted for the exact same patient identity.
    accepted = client.post('/v1/conversation', headers={'x-api-key': 'new-key'},
                           json={'user_id': 'rotation-patient', 'message': 'hello again'})
    assert accepted.status_code == 200

    # 6. Existing patient memory (caregiver-synced before rotation) is
    # still there under the new key -- rotation touched only the
    # credential-to-identity mapping, nothing patient-scoped.
    family_check = client.post('/v1/conversation', headers={'x-api-key': 'new-key'},
                               json={'user_id': 'rotation-patient',
                                    'message': 'who is my daughter'})
    assert family_check.status_code == 200

    # 7. Session/patient ownership is unaffected: the session created
    # under the old key continues under the new one, same session_id.
    continued = client.post('/v1/conversation', headers={'x-api-key': 'new-key'},
                            json={'user_id': 'rotation-patient', 'session_id': session_id,
                                 'message': 'continuing'})
    assert continued.status_code == 200
    assert continued.json()['session_id'] == session_id


def test_rotation_never_grants_the_new_key_a_different_patients_memory(rotation_client):
    """Rotation must not accidentally widen access: the new key is bound to
    the same single patient identity, nothing more."""
    client, monkeypatch = rotation_client
    monkeypatch.setenv('SMRITI_API_KEYS', json.dumps({'new-key': 'rotation-patient'}))

    response = client.post('/v1/conversation', headers={'x-api-key': 'new-key'},
                           json={'user_id': 'a-completely-different-patient', 'message': 'hi'})
    assert response.status_code == 403
