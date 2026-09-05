"""Shared API dependencies: authentication, rate limiting, request identity."""
from __future__ import annotations

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


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    """Fail closed.

    If no server key is configured the service refuses to serve protected routes,
    unless the operator has explicitly opted into unauthenticated local use.
    """
    app = application(request)
    expected = os.getenv(app.config.api_key_env)

    if not expected:
        if not app.config.allow_unauthenticated:
            raise HTTPException(503, 'API authentication is not configured')
        principal = 'anonymous'
    else:
        if not x_api_key or not _constant_time_equals(x_api_key, expected):
            raise HTTPException(401, 'Invalid API key')
        principal = 'api-key'

    # A shared API key authenticates the service client. Personal endpoints use
    # authenticated_user_id below to add the user binding before memory/session
    # access; the legacy command endpoint remains API-key compatible.
    bound_user_id = (os.getenv('SMRITI_AUTH_USER_ID') or '').strip()
    if bound_user_id:
        request.state.authenticated_user_id = bound_user_id

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
