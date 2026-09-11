"""Closes concrete test-coverage gaps the forensic audit found: rate
limiting was never exercised end-to-end, and malformed SMRITI_API_KEYS was
never proven to fail closed via a real HTTP request.
"""
from __future__ import annotations

from smriti_voice.api.dependencies import RateLimiter


# --------------------------------------------------------------------------- #
# RateLimiter: pure unit test of the sliding-window logic itself
# --------------------------------------------------------------------------- #
def test_rate_limiter_allows_up_to_the_configured_limit_then_blocks():
    limiter = RateLimiter(per_minute=3)
    assert limiter.check('someone') is True
    assert limiter.check('someone') is True
    assert limiter.check('someone') is True
    assert limiter.check('someone') is False  # the 4th request in the window is refused


def test_rate_limiter_is_isolated_per_key():
    limiter = RateLimiter(per_minute=1)
    assert limiter.check('user-a') is True
    assert limiter.check('user-a') is False
    assert limiter.check('user-b') is True  # a different key has its own budget


def test_rate_limiter_disabled_when_non_positive():
    limiter = RateLimiter(per_minute=0)
    for _ in range(50):
        assert limiter.check('anyone') is True


# --------------------------------------------------------------------------- #
# End-to-end: the limiter actually fires a real 429 through the dependency
# --------------------------------------------------------------------------- #
def test_conversation_endpoint_returns_429_once_the_limit_is_exceeded(client, auth_headers):
    import smriti_voice.api.dependencies as deps
    deps._limiter = RateLimiter(per_minute=2)  # tiny limit, installed directly

    body = {'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'}
    first = client.post('/v1/conversation', json=body, headers=auth_headers)
    second = client.post('/v1/conversation', json=body, headers=auth_headers)
    third = client.post('/v1/conversation', json=body, headers=auth_headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert third.status_code == 429


# --------------------------------------------------------------------------- #
# SMRITI_API_KEYS malformed at the HTTP layer: must fail closed (503), never
# silently fall back to single-key mode or open access.
# --------------------------------------------------------------------------- #
def test_malformed_api_keys_json_fails_closed_with_503(client, auth_headers, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEYS', 'not valid json{{{')
    resp = client.post('/v1/conversation',
                       json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                       headers=auth_headers)
    assert resp.status_code == 503


def test_empty_api_keys_object_fails_closed_with_503(client, auth_headers, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEYS', '{}')
    resp = client.post('/v1/conversation',
                       json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                       headers=auth_headers)
    assert resp.status_code == 503


def test_api_keys_with_empty_list_value_fails_closed_with_503(client, auth_headers, monkeypatch):
    monkeypatch.setenv('SMRITI_API_KEYS', '{"some-key": []}')
    resp = client.post('/v1/conversation',
                       json={'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'},
                       headers=auth_headers)
    assert resp.status_code == 503
