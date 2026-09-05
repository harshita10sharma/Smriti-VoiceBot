"""HTTP surface: authentication, upload validation, and the endpoint contracts."""
from __future__ import annotations

import pytest

from smriti_voice.tts.mock import silent_wav

VALID_WAV = silent_wav(0.4)


# --------------------------------------------------------------------------- #
# Authentication
# --------------------------------------------------------------------------- #
def test_health_and_languages_are_public(client):
    assert client.get('/v1/health').status_code == 200
    assert client.get('/v1/languages').status_code == 200


def test_health_never_returns_a_credential(client, monkeypatch):
    monkeypatch.setenv('SARVAM_API_KEY', 'sk_this_must_never_appear')
    body = client.get('/v1/health').text
    assert 'sk_this_must_never_appear' not in body


@pytest.mark.parametrize('path,payload', [
    ('/v1/conversation', {'user_id': 'demo-user', 'message': 'hello'}),
    ('/v1/tools', None),
])
def test_protected_endpoints_reject_a_missing_key(client, path, payload):
    response = client.post(path, json=payload) if payload else client.get(path)
    assert response.status_code == 401


def test_protected_endpoints_reject_a_wrong_key(client):
    response = client.post('/v1/conversation', headers={'x-api-key': 'wrong'},
                           json={'user_id': 'demo-user', 'message': 'hello'})
    assert response.status_code == 401


def test_valid_key_is_accepted(client, auth_headers):
    response = client.post('/v1/conversation', headers=auth_headers,
                           json={'user_id': 'demo-user', 'message': 'help'})
    assert response.status_code == 200
    assert response.json()['action'] == 'HELP'


def test_service_fails_closed_when_no_key_is_configured(app, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.delenv('SMRITI_API_KEY', raising=False)
    client = TestClient(create_app(app))
    response = client.post('/v1/conversation', json={'user_id': 'demo-user', 'message': 'hi'})
    assert response.status_code == 503


def test_service_fails_closed_when_authenticated_identity_is_not_configured(app, monkeypatch):
    from fastapi.testclient import TestClient
    from smriti_voice.api.app import create_app
    monkeypatch.setenv('SMRITI_API_KEY', 'test-server-key')
    monkeypatch.delenv('SMRITI_AUTH_USER_ID', raising=False)
    client = TestClient(create_app(app))
    response = client.post('/v1/conversation', headers={'x-api-key': 'test-server-key'},
                           json={'user_id': 'demo-user', 'message': 'hi'})
    assert response.status_code == 503


def test_protected_conversation_cannot_impersonate_another_user(client, auth_headers):
    response = client.post('/v1/conversation', headers=auth_headers,
                           json={'user_id': 'other-user', 'message': 'show my family'})
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# Upload validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('payload,content_type,expected', [
    pytest.param(b'', 'audio/wav', 400, id='empty-file'),
    pytest.param(b'not-a-wav-at-all', 'audio/wav', 415, id='invalid-wav'),
    pytest.param(b'RIFF' + b'\x00' * 4 + b'WAVE', 'audio/wav', 415, id='truncated-wav'),
    pytest.param(VALID_WAV, 'text/plain', 415, id='wrong-content-type'),
    pytest.param(b'RIFF' + b'\x00' * 40 + b'WAVE', 'audio/wav', 415, id='corrupt-header'),
])
def test_bad_uploads_fail_cleanly(client, auth_headers, payload, content_type, expected):
    for path, data in (('/v1/conversation/voice', {'user_id': 'demo-user'}),
                       ('/v1/command', {'language': 'eng'})):
        response = client.post(path, headers=auth_headers, data=data,
                               files={'audio_wav': ('a.wav', payload, content_type)})
        assert response.status_code == expected, f'{path} accepted a bad upload'


def test_oversized_upload_is_rejected(client, auth_headers, monkeypatch):
    oversized = VALID_WAV + b'\x00' * (11 * 1024 * 1024)
    response = client.post('/v1/conversation/voice', headers=auth_headers,
                           data={'user_id': 'demo-user'},
                           files={'audio_wav': ('big.wav', oversized, 'audio/wav')})
    assert response.status_code == 413


# --------------------------------------------------------------------------- #
# Contracts
# --------------------------------------------------------------------------- #
def test_conversation_response_shape(client, auth_headers):
    body = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'message': 'open play'}).json()
    for field in ('request_id', 'session_id', 'response_text', 'language', 'kind',
                  'action', 'action_accepted', 'metadata'):
        assert field in body
    assert body['metadata']['total_latency_ms'] >= 0


def test_conversation_rejects_a_malformed_user_id(client, auth_headers):
    response = client.post('/v1/conversation', headers=auth_headers,
                           json={'user_id': 'a b; drop', 'message': 'hello'})
    assert response.status_code == 422


def test_languages_endpoint_reports_the_matrix(client):
    body = client.get('/v1/languages').json()
    assert body['count'] == 15
    assert body['summary']['SUPPORTED'] == 0     # nothing validated yet
    assamese = next(item for item in body['languages'] if item['code'] == 'asm')
    assert assamese['tts_online'] is False


def test_tools_endpoint_marks_sensitive_tools_as_not_executable(client, auth_headers):
    body = client.get('/v1/tools', headers=auth_headers).json()
    blocked = [tool for tool in body['tools'] if not tool['executable_by_voice']]
    assert {tool['name'] for tool in blocked} == {'change_medication', 'delete_record',
                                                  'transfer_money', 'call_number'}
    for tool in blocked:
        assert tool['name'] not in body['advertised_to_model']


def test_audio_endpoint_requires_authentication(client):
    """Auth is checked before the id is looked at, so ids cannot be enumerated."""
    assert client.get('/v1/audio/deadbeef').status_code == 401


def test_audio_endpoint_rejects_a_path_traversal_id(client, auth_headers):
    for bad in ('..%2F..%2Fetc%2Fpasswd', 'notahexid', 'ABCDEF', '../../etc/passwd'):
        assert client.get(f'/v1/audio/{bad}', headers=auth_headers).status_code == 404


def test_unsafe_request_is_refused_over_http(client, auth_headers):
    body = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'message': 'transfer money'}).json()
    assert body['kind'] == 'REFUSAL' and body['action'] == 'NO_ACTION'
    assert body['safety']['allowed'] is False
