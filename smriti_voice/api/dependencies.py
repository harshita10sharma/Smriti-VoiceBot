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


def _parse_api_key_map(raw: str) -> dict[str, str | list[str] | dict]:
    """Parse ``SMRITI_API_KEYS`` into one of three value shapes per key:

    - a string: the original single-patient-per-key mode.
    - a list of strings: a fixed, explicit allow-list -- that one key may
      act as any of the listed (and only the listed) user ids.
    - an object ``{"dynamic": true, "user_ids": [...]}`` (``user_ids``
      optional): a *dynamic* backend key. Its authorized set is the union
      of ``user_ids`` here (an optional seed list, e.g. known pilot
      patients) and whatever this exact credential has been durably
      granted in the database via ``POST /v1/memory/sync`` -- see
      ``backend_key_grants`` in database/migrations.py and
      MemoryRepository.grant_backend_key_access. This is what lets a
      production Backend credential gain a genuinely new patient without
      an env-var edit and a process restart, while every individual grant
      remains one explicit, authenticated, auditable (key, user_id) row --
      never a wildcard evaluated at request time.

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
                         '{api_key: user_id}, {api_key: [user_id, ...]}, or '
                         '{api_key: {"dynamic": true, "user_ids": [...]}}')
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
        elif isinstance(value, dict):
            if value.get('dynamic') is not True:
                raise ValueError('SMRITI_API_KEYS object values must have "dynamic": true')
            seed = value.get('user_ids', [])
            if not isinstance(seed, list) or not all(isinstance(v, str) and v.strip()
                                                      for v in seed):
                raise ValueError('SMRITI_API_KEYS "user_ids" must be a list of non-empty '
                                 'user id strings if present')
            extra = set(value) - {'dynamic', 'user_ids'}
            if extra:
                raise ValueError(f'SMRITI_API_KEYS object values have unknown fields: {extra}')
        else:
            raise ValueError('SMRITI_API_KEYS values must be a string, a list of strings, '
                             'or a {"dynamic": true, ...} object')
    return parsed


def require_api_key(request: Request, x_api_key: str | None = Header(default=None)) -> str:
    """Fail closed.

    Two mutually exclusive modes, chosen by which variable is set:

    - Multi-user (``SMRITI_API_KEYS``, a JSON object). Each key's value is a
      single user id (one key, one patient — a caller can never claim a
      different ``user_id``), a list of user ids (one backend key, a fixed,
      explicit allow-list of patients), or a ``{"dynamic": true, ...}``
      object (one backend key whose allow-list is the union of an optional
      seed list and whatever has been durably *granted* to this exact
      credential — see ``POST /v1/memory/sync`` and
      MemoryRepository.grant_backend_key_access). In every case,
      ``authorized_user_ids`` below is what every route must check
      membership against; it is never "this key plus any user_id the
      caller sends."
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
        matched_value: str | list[str] | dict | None = None
        for key, value in key_map.items():
            if x_api_key and _constant_time_equals(x_api_key, key):
                matched_key, matched_value = key, value
                break
        if matched_value is None:
            raise HTTPException(401, 'Invalid API key')

        key_hash = hashlib.sha256(matched_key.encode('utf-8')).hexdigest()
        if isinstance(matched_value, dict):
            seed = frozenset(matched_value.get('user_ids', []))
            granted = app.memory.repo.backend_key_granted_user_ids(key_hash)
            authorized = seed | granted
            # A dynamic key that has granted no patient yet (a brand-new
            # deployment) is not an error: it is authorized for nothing
            # until its first POST /v1/memory/sync call, exactly like the
            # existing "auto-provision on first contact" behavior for a
            # single user_id -- see memory_sync's own docstring.
            request.state.dynamic_key_hash = key_hash
        else:
            authorized = frozenset(matched_value) if isinstance(matched_value, list) \
                else frozenset({matched_value})
        request.state.authorized_user_ids = authorized
        if len(authorized) == 1:
            # Single-target key (string form, or a one-element list/grant
            # set): fully backward compatible with authenticated_user_id()
            # and every route that hasn't been updated to check
            # authorized_user_ids.
            request.state.authenticated_user_id = next(iter(authorized))
            principal = next(iter(authorized))
        else:
            # A genuinely multi-patient key (fixed list or dynamic).
            # Deliberately do NOT set authenticated_user_id: any route that
            # only knows how to check a single identity must fail closed
            # (503) rather than guess which of several authorized patients
            # this request is for. Rate-limit by the key itself, hashed —
            # never the raw key.
            principal = 'backend:' + key_hash[:16]
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

    A brand-new *dynamic* key that has not granted any patient yet
    legitimately produces an empty set — that is not a misconfiguration,
    it is the expected state before its first successful
    POST /v1/memory/sync (see dynamic_key_hash / memory_sync.py). Only the
    genuine absence of the attribute (the auth dependency never ran, or ran
    down a path that never sets it at all) is treated as unconfigured.
    """
    ids = getattr(request.state, 'authorized_user_ids', None)
    if ids is None:
        raise HTTPException(503, 'Authenticated user identity is not configured')
    return ids


def dynamic_key_hash(request: Request) -> str | None:
    """The full SHA-256 hash of the authenticated credential if (and only
    if) it is a "dynamic" backend key, else ``None``. Used exclusively by
    ``POST /v1/memory/sync`` to allow provisioning a genuinely new patient
    for such a key and to record the grant afterward -- every other route
    still only ever checks membership in ``authorized_user_ids``."""
    return getattr(request.state, 'dynamic_key_hash', None)


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
