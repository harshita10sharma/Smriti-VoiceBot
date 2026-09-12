"""GET /v1/health is a liveness check, not a readiness check gated on
optional providers -- verified, not assumed. It must report 'ok' even
when every optional cloud provider (Gemini/OpenAI/Sarvam/TTS/etc.) is
unconfigured, since those are intentionally optional per deployment, and a
health check that failed for that reason would make an uptime monitor or
Render's healthCheckPath kill a perfectly healthy process. Provider
availability is reported separately, as booleans, in the same response --
callers that need readiness-style behavior can inspect those directly.
"""
from __future__ import annotations


def test_health_reports_ok_with_zero_optional_providers_configured(client, monkeypatch):
    for var in ('SARVAM_API_KEY', 'GEMINI_API_KEY', 'OPENAI_API_KEY', 'GROQ_API_KEY'):
        monkeypatch.delenv(var, raising=False)
    resp = client.get('/v1/health')
    assert resp.status_code == 200
    assert resp.json()['status'] == 'ok'


def test_health_never_returns_a_credential_value(client):
    body = client.get('/v1/health').json()
    creds = body['providers']['credentials_configured']
    for value in creds.values():
        assert isinstance(value, bool)  # booleans only, never a key string


def test_health_requires_no_authentication(client):
    resp = client.get('/v1/health')
    assert resp.status_code == 200  # no x-api-key sent
