"""Swagger/OpenAPI must correctly represent x-api-key.

FastAPI does not automatically recognize a plain Header()-based Depends()
(api/dependencies.py::require_api_key) as an authentication requirement --
without an explicit fix, the generated OpenAPI document has no
securitySchemes and no per-operation `security`, so Swagger UI shows no
"Authorize" button and marks x-api-key as an optional header on every
route, both misleading for anyone using /docs to test manually. This does
not change the actual authentication mechanism or enforcement (still
x-api-key, still enforced the same way at runtime) -- only what the
generated document describes.
"""
from __future__ import annotations

PROTECTED_PATHS = [
    ('post', '/v1/conversation'), ('post', '/v1/conversation/welcome'),
    ('post', '/v1/conversation/voice'), ('get', '/v1/audio/{audio_id}'),
    ('get', '/v1/voice/jobs/{job_id}'), ('post', '/v1/voice/jobs/{job_id}/cancel'),
    ('get', '/v1/tools'), ('post', '/v1/command'), ('post', '/v1/memory/sync'),
]
PUBLIC_PATHS = [('get', '/v1/health'), ('get', '/v1/languages'),
                ('get', '/v1/languages/{code}')]


def test_openapi_declares_the_api_key_security_scheme(client):
    schema = client.get('/openapi.json').json()
    schemes = schema.get('components', {}).get('securitySchemes', {})
    assert 'ApiKeyAuth' in schemes
    assert schemes['ApiKeyAuth']['type'] == 'apiKey'
    assert schemes['ApiKeyAuth']['in'] == 'header'
    assert schemes['ApiKeyAuth']['name'] == 'x-api-key'


def test_every_protected_endpoint_declares_the_security_requirement(client):
    schema = client.get('/openapi.json').json()
    for method, path in PROTECTED_PATHS:
        operation = schema['paths'][path][method]
        assert operation.get('security') == [{'ApiKeyAuth': []}], (
            f'{method.upper()} {path} is protected by require_api_key at runtime '
            f'but OpenAPI does not say so')


def test_public_endpoints_declare_no_security_requirement(client):
    schema = client.get('/openapi.json').json()
    for method, path in PUBLIC_PATHS:
        operation = schema['paths'][path][method]
        assert operation.get('security') is None, (
            f'{method.upper()} {path} is genuinely unauthenticated at runtime but '
            f'OpenAPI incorrectly implies it needs a credential')


def test_docs_and_openapi_json_are_reachable_without_authentication(client):
    # Swagger itself must be browsable without already holding a credential
    # -- otherwise it cannot be used to discover how to authenticate.
    assert client.get('/docs').status_code == 200
    assert client.get('/openapi.json').status_code == 200
