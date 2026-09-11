"""Shared API dependencies: authentication, rate limiting, request identity."""
from __future__ import annotations

import hashlib
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


def _parse_api_key_map(raw: str) -> dict[str, str | list[str]]:
    """Parse ``SMRITI_API_KEYS`` into ``{api_key: user_id}`` or
    ``{api_key: [user_id, ...]}``.

    A string value is the original single-patient-per-key mode. A list value
    is the backend/multi-patient mode: that one key may act as any of the
    listed (and only the listed) user ids — an explicit allow-list, never
    "this key plus any user_id the caller sends."

    Raises ``ValueError`` on anything malformed — callers must treat that as a
    fail-closed 503, never as "fall back to single-key mode", so a typo in
    this variable cannot silently downgrade a multi-user deployment.
    """
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise ValueError('SMRITI_API_KEYS is not valid JSON') from exc
    if not isinstance(parsed, dict) or not parsed:
        raise ValueError('SMRITI_API_KEYS must be a non-empty JSON object of '
                         '{api_key: user_id} or {api_key: [user_id, ...]}')
    for key, value in parsed.items():
        if not isinstance(key, str) or not key.strip():
            raise ValueError('SMRITI_API_KEYS keys must be non-empty strings')
        if isinstance(value, str):
            if not value.strip():
                raise ValueError('SMRITI_API_KEYS string values must be non-empty')
        elif isinstance(value, list):
            if not value or not all(isinstance(v, str) and v.strip() for v in value):
                raise ValueError('SMRITI_API_KEYS list values must be a non-empty list '
                                 'of non-empty user id strings')
        else:
            raise ValueError('SMRITI_API_KEYS values must be a string or a list of strings')
    return parsed


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    """Fail closed.

    Two mutually exclusive modes, chosen by which variable is set:

    - Multi-user (``SMRITI_API_KEYS``, a JSON object). Each key's value is
      either a single user id (one key, one patient — a caller can never
      claim a different ``user_id``) or a list of user ids (one backend key,
      an explicit allow-list of patients — a caller may act as any id in
      that list, and only those). Either way, ``authorized_user_ids`` below
      is what every route must check membership against; it is never "this
      key plus any user_id the caller sends."
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

        matched_key = None
        matched_value: str | list[str] | None = None
        for key, value in key_map.items():
            if x_api_key and _constant_time_equals(x_api_key, key):
                matched_key, matched_value = key, value
                break
        if matched_value is None:
            raise HTTPException(401, 'Invalid API key')

        authorized = frozenset(matched_value) if isinstance(matched_value, list) \
            else frozenset({matched_value})
        request.state.authorized_user_ids = authorized
        if len(authorized) == 1:
            # Single-target key (string form, or a one-element list): fully
            # backward compatible with authenticated_user_id() and every
            # route that hasn't been updated to check authorized_user_ids.
            request.state.authenticated_user_id = next(iter(authorized))
            principal = next(iter(authorized))
        else:
            # A genuinely multi-patient key. Deliberately do NOT set
            # authenticated_user_id: any route that only knows how to check
            # a single identity must fail closed (503) rather than guess
            # which of several authorized patients this request is for.
            # Rate-limit by the key itself, hashed — never the raw key.
            principal = 'backend:' + hashlib.sha256(matched_key.encode('utf-8')).hexdigest()[:16]
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
            request.state.authorized_user_ids = frozenset({bound_user_id})
            principal = bound_user_id

    if not get_limiter(app).check(principal):
        raise HTTPException(429, 'Too many requests')
    return principal


def authenticated_user_id(request: Request) -> str:
    """Return the user identity established by the authentication dependency.

    Only meaningful when exactly one identity is authorized (single-user
    mode, or a single-target multi-user key). A genuinely multi-patient
    backend key never sets this — see authorized_user_ids below.
    """
    user_id = getattr(request.state, 'authenticated_user_id', None) or \
        (os.getenv('SMRITI_AUTH_USER_ID') or '').strip()
    if not user_id:
        raise HTTPException(503, 'Authenticated user identity is not configured')
    return user_id


def authorized_user_ids(request: Request) -> frozenset[str]:
    """The set of user ids the authenticated caller may act as.

    Single-user mode and a single-target multi-user key both produce a
    one-element set. A backend key authorized for several explicit patients
    produces the full allow-list. Routes must check membership (``in``),
    never assume there is exactly one. Fails closed (503) if authentication
    never established any authorization at all — it does not guess.
    """
    ids = getattr(request.state, 'authorized_user_ids', None)
    if not ids:
        raise HTTPException(503, 'Authenticated user identity is not configured')
    return ids


def _constant_time_equals(left: str, right: str) -> bool:
    import hmac
    return hmac.compare_digest(left.encode('utf-8'), right.encode('utf-8'))


def idempotency_key(x_idempotency_key: str | None = Header(default=None)) -> str | None:
    """An optional client-supplied key for POST /v1/conversation and
    POST /v1/conversation/voice (see smriti_voice/idempotency.py). A
    malformed value is treated as absent rather than rejected outright --
    this feature is purely additive, so a client that gets it slightly
    wrong should fall back to "no idempotency protection", never to an
    error the old contract never had."""
    if not x_idempotency_key:
        return None
    key = x_idempotency_key.strip()
    if not key or len(key) > 128 or not all(ch.isalnum() or ch in '-_.:' for ch in key):
        return None
    return key


def ensure_patient_active(app: Application, user_id: str) -> None:
    """Deny a caregiver/backend-disabled patient even though the caller's
    API key still authorizes that user_id -- authorization (the key's
    allow-list) and provisioning/account-status are deliberately separate
    concerns. A user_id with no row at all is never treated as denied here:
    that is the normal state before a first caregiver sync or a first
    conversation turn, not a disabled account."""
    if not app.memory.repo.is_user_active(user_id):
        raise HTTPException(403, 'This patient account is disabled')
