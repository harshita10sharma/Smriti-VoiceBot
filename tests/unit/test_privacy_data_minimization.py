"""Automated checks that API responses and logs never expose secrets,
credentials, phone numbers, raw audio, database internals, or another
patient's data. Synthetic test data only.
"""
from __future__ import annotations

import json
import logging


def test_health_response_contains_no_credential_values(client, monkeypatch):
    monkeypatch.setenv('SARVAM_API_KEY', 'sk-real-looking-secret-value-should-never-appear')
    body = client.get('/v1/health').json()
    dumped = json.dumps(body)
    assert 'sk-real-looking-secret-value-should-never-appear' not in dumped


def test_401_response_never_echoes_the_submitted_key(client):
    resp = client.post('/v1/conversation',
                       json={'user_id': 'demo-user', 'message': 'hi', 'language': 'eng'},
                       headers={'x-api-key': 'attempted-key-abc123'})
    assert resp.status_code == 401
    assert 'attempted-key-abc123' not in resp.text


def test_500_response_never_contains_a_stack_trace_or_python_exception_repr(client, auth_headers,
                                                                            app, monkeypatch):
    """Force a genuine internal failure and confirm the response body stays
    a fixed, safe string -- not str(exc) or a traceback."""
    def _boom(*args, **kwargs):
        raise RuntimeError('/very/secret/internal/path/leaked in a traceback would be bad')

    monkeypatch.setattr(app.memory.repo, 'sync_caregiver_memory', _boom)
    resp = client.post('/v1/memory/sync',
                       json={'user_id': 'demo-user', 'family_members': [], 'medicines': [],
                            'daily_routines': []},
                       headers=auth_headers)
    assert resp.status_code == 500
    assert 'secret' not in resp.text.lower()
    assert 'Traceback' not in resp.text
    assert '.py' not in resp.text  # no file path leakage


def test_memory_sync_never_persists_a_raw_phone_number_even_if_smuggled(client, auth_headers):
    resp = client.post('/v1/memory/sync',
                       json={'user_id': 'demo-user',
                            'family_members': [{'name': 'X', 'relationship': 'son',
                                                'phone_available': True,
                                                'phone_number': '+919876543210'}],
                            'medicines': [], 'daily_routines': []},
                       headers=auth_headers)
    assert resp.status_code == 422  # rejected outright, never silently dropped-and-stored


def test_family_member_payload_never_includes_a_phone_number_field(app):
    from smriti_voice.memory.models import FamilyMember
    app.memory.repo.add_family_member(FamilyMember(
        user_id='demo-user', name='PrivacyCheckPerson', relation='son',
        phone='+919999999999', is_trusted_contact=True))
    result = app.memory.find_family('demo-user', name='PrivacyCheckPerson')
    payload = result[0]
    assert 'phone' not in payload
    assert 'phone_number' not in payload
    assert '+919999999999' not in json.dumps(payload)


def test_cross_patient_memory_is_never_returned(app):
    """A tool call scoped to one user_id must structurally never see
    another patient's row, verified at the service layer directly."""
    from smriti_voice.memory.models import FamilyMember, User
    app.memory.repo.upsert_user(User(user_id='other-privacy-patient', display_name='Other'))
    app.memory.repo.add_family_member(FamilyMember(
        user_id='other-privacy-patient', name='SecretRelative', relation='son'))

    result = app.memory.list_family('demo-user')
    names = {m['name'] for m in result}
    assert 'SecretRelative' not in names


def test_log_formatter_redacts_bearer_tokens_and_api_style_keys():
    from smriti_voice.logging import redact
    text = 'Authorization: Bearer abcdefgh12345678 and sk-abcdefghijklmnop'
    redacted = redact(text)
    assert 'abcdefgh12345678' not in redacted
    assert 'sk-abcdefghijklmnop' not in redacted


def test_log_formatter_never_includes_forbidden_audio_keys():
    from smriti_voice.logging import redact
    payload = {'audio': b'\x00\x00binarydata', 'raw_audio': b'more', 'note': 'fine to log'}
    redacted = redact(payload)
    assert 'audio' not in redacted
    assert 'raw_audio' not in redacted
    assert redacted.get('note') == 'fine to log'


def test_conversation_response_never_leaks_a_sql_statement_shape(client, auth_headers, app,
                                                                  monkeypatch):
    """A defensive check: even if a future change accidentally formats an
    error message from a raised DB exception, it must not reach the
    client verbatim for a 500."""
    import sqlite3

    def _boom(*args, **kwargs):
        raise sqlite3.OperationalError(
            "near \"SELECT * FROM users WHERE user_id = 'demo-user'\": syntax error")

    monkeypatch.setattr(app.memory.repo, 'sync_caregiver_memory', _boom)
    resp = client.post('/v1/memory/sync',
                       json={'user_id': 'demo-user', 'family_members': [], 'medicines': [],
                            'daily_routines': []},
                       headers=auth_headers)
    assert 'SELECT' not in resp.text
    assert 'syntax error' not in resp.text
