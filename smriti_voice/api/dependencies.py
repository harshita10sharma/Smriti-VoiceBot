"""Shared API dependencies: authentication, rate limiting, request identity."""
from __future__ import annotations

import json
import os
import time
import uuid
from collections import defaultdict, deque
from typing import Deque

from fastapi import Header, HTTPException, Request

from ..app import Application, get_application
from ..logging import get_logger

log = get_logger('api.auth')


def application(request: Request) -> Application:
    """The app built at startup, or the process-wide default."""
    return getattr(request.app.state, 'application', None) or get_application()


def request_id(x_request_id: str | None = Header(default=None)) -> str:
    """Client-supplied ids are accepted but sanitised: they end up in logs."""
    if x_request_id and x_request_id.isalnum() and len(x_request_id) <= 64:
        return x_request_id
    return uuid.uuid4().hex


class RateLimiter:
    """Fixed-window per-key limiter.  Enough for a device-facing service."""

    def __init__(self, per_minute: int = 60) -> None:
        self.per_minute = per_minute
        self._hits: dict[str, Deque[float]] = defaultdict(deque)

    def check(self, key: str) -> bool:
        if self.per_minute <= 0:
            return True
        now = time.time()
        window = self._hits[key]
        while window and now - window[0] > 60:
            window.popleft()
        if len(window) >= self.per_minute:
            return False
        window.append(now)
        return True


_limiter: RateLimiter | None = None


def get_limiter(app: Application) -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(app.config.rate_limit_per_minute)
    return _limiter


def _parse_api_key_map(raw: str) -> dict[str, str]:
    """Parse ``SMRITI_API_KEYS`` into ``{api_key: user_id}``.

    Raises ``ValueError`` on anything malformed — callers must treat that as a
    fail-closed 503, never as "fall back to single-key mode", so a typo in
    this variable cannot silently downgrade a multi-user deployment.
    """
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValueError('SMRITI_API_KEYS is not valid JSON') from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError('SMRITI_API_KEYS must be a non-empty JSON object of {api_key: user_id}')
    for key, user_id in parsed.items():
        if not isinstance(key, str) or not isinstance(user_id, str) \
                or not key.strip() or not user_id.strip():
            raise ValueError('SMRITI_API_KEYS keys and values must be non-empty strings')
    return parsed


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    """Fail closed.

    Two mutually exclusive modes, chosen by which variable is set:

    - Multi-user (``SMRITI_API_KEYS``, a JSON object of ``{api_key: user_id}``):
      each key is bound to its own user id. A caller authenticates as exactly
      the identity its own key maps to — it can never claim a different
      ``user_id`` in the request body, because every route compares that
      field against the identity established here (see
      ``authenticated_user_id`` below and the ``user_id != authenticated_id``
      checks in the route handlers). This is what lets one backend serve many
      elderly users through one VoiceBot deployment.
    - Single-user (``SMRITI_API_KEY`` + ``SMRITI_AUTH_USER_ID``): one shared
      key bound to one fixed identity — the original design, unchanged, for a
      deployment that serves exactly one elder.

    If no server key is configured at all the service refuses to serve
    protected routes, unless the operator has explicitly opted into
    unauthenticated local use.
    """
    app = application(request)
    raw_key_map = (os.getenv('SMRITI_API_KEYS') or '').strip()

    if raw_key_map:
        try:
            key_map = _parse_api_key_map(raw_key_map)
        except ValueError as exc:
            log.error('smriti_api_keys_misconfigured', fields={'error': str(exc)})
            raise HTTPException(503, 'API authentication is not configured correctly') from exc

        matched_user_id = next(
            (user_id for key, user_id in key_map.items()
             if x_api_key and _constant_time_equals(x_api_key, key)),
            None)
        if matched_user_id is None:
            raise HTTPException(401, 'Invalid API key')
        request.state.authenticated_user_id = matched_user_id
        principal = matched_user_id
    else:
        expected = os.getenv(app.config.api_key_env)
        if not expected:
            if not app.config.allow_unauthenticated:
                raise HTTPException(503, 'API authentication is not configured')
            principal = 'anonymous'
        else:
            if not x_api_key or not _constant_time_equals(x_api_key, expected):
                raise HTTPException(401, 'Invalid API key')
            principal = 'api-key'

        # A shared API key authenticates the service client. Personal endpoints
        # use authenticated_user_id below to add the user binding before
        # memory/session access; the legacy command endpoint remains
        # API-key compatible.
        bound_user_id = (os.getenv('SMRITI_AUTH_USER_ID') or '').strip()
        if bound_user_id:
            request.state.authenticated_user_id = bound_user_id
            principal = bound_user_id

    if not get_limiter(app).check(principal):
        raise HTTPException(429, 'Too many requests')
    return principal


def authenticated_user_id(request: Request) -> str:
    """Return the user identity established by the authentication dependency."""
    user_id = getattr(request.state, 'authenticated_user_id', None) or \
        (os.getenv('SMRITI_AUTH_USER_ID') or '').strip()
    if not user_id:
        raise HTTPException(503, 'Authenticated user identity is not configured')
    return user_id


def _constant_time_equals(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode('utf-8'), right.encode('utf-8'))
