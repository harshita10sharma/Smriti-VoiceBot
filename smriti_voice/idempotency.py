"""Optional request-level idempotency for endpoints that can execute a
controlled action or mutate memory (``POST /v1/conversation`` and
``POST /v1/conversation/voice``).

A client on a flaky connection may resend the same logical request after a
timeout. Without protection, that resend would run the conversation turn a
second time -- including any controlled action a pending confirmation had
already resolved, such as a phone call or a saved reminder. A client that
opts in by sending an ``Idempotency-Key`` header gets a guarantee that the
same key with the same request body is executed exactly once; a client that
sends nothing behaves exactly as before this module existed.

Backed by the same SQLite database as everything else -- no new store, no
new connection-management pattern. A request claims its key with an atomic
``INSERT ... ON CONFLICT DO NOTHING`` before doing any work, the same
single-flight technique ``VoiceJobRepository.mark_processing`` already uses
via a conditional ``UPDATE``.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from .database.connection import Database

_DT_FORMAT = '%Y-%m-%d %H:%M:%S'


def payload_hash(*parts: Any) -> str:
    """A stable hash of the parts of a request that must match for a
    replayed idempotency key to be considered "the same request"."""
    digest = hashlib.sha256()
    for part in parts:
        digest.update(b'\x00')
        digest.update(part if isinstance(part, bytes) else str(part).encode('utf-8'))
    return digest.hexdigest()


@dataclass
class IdempotencyOutcome:
    status: str  # 'new' | 'replay' | 'in_progress' | 'conflict'
    response_json: str | None = None


class IdempotencyStore:
    def __init__(self, database: Database, *, ttl_hours: int = 24) -> None:
        self.db = database
        self.ttl_hours = ttl_hours

    def begin(self, user_id: str, key: str, hash_value: str) -> IdempotencyOutcome:
        """Atomically claim ``(user_id, key)``.

        - No existing row: this call claims it -- the caller must do the
          work and then call :meth:`complete` (or :meth:`abandon` on
          failure). Returns ``'new'``.
        - An existing row with a different ``payload_hash``: the same key
          was reused for a materially different request. Returns
          ``'conflict'`` -- the caller must reject it rather than guess
          which request the caller actually meant.
        - An existing row with the same hash but no response yet: another
          request with this exact key is still being processed. Returns
          ``'in_progress'``.
        - An existing row with the same hash and a stored response: the
          original request already completed. Returns ``'replay'`` with the
          stored response so the caller executes nothing a second time.
        """
        self._prune()
        with self.db.connect() as connection:
            cursor = connection.execute(
                """INSERT INTO idempotent_requests (user_id, idempotency_key, payload_hash)
                   VALUES (?,?,?)
                   ON CONFLICT(user_id, idempotency_key) DO NOTHING""",
                (user_id, key, hash_value))
            if cursor.rowcount > 0:
                return IdempotencyOutcome(status='new')
            row = connection.execute(
                """SELECT payload_hash, response_json FROM idempotent_requests
                   WHERE user_id = ? AND idempotency_key = ?""",
                (user_id, key)).fetchone()
        if row is None:
            # A concurrent request finished and pruned between the failed
            # insert and this read -- vanishingly unlikely, and treating it
            # as 'new' is safe (worst case: one extra execution, not zero
            # protection).
            return IdempotencyOutcome(status='new')
        if row['payload_hash'] != hash_value:
            return IdempotencyOutcome(status='conflict')
        if row['response_json'] is None:
            return IdempotencyOutcome(status='in_progress')
        return IdempotencyOutcome(status='replay', response_json=row['response_json'])

    def complete(self, user_id: str, key: str, response_json: str) -> None:
        with self.db.connect() as connection:
            connection.execute(
                """UPDATE idempotent_requests SET response_json = ?
                   WHERE user_id = ? AND idempotency_key = ?""",
                (response_json, user_id, key))

    def abandon(self, user_id: str, key: str) -> None:
        """Release a claimed key after the request failed before producing a
        response, so a genuine retry is not stuck reporting 'in_progress'
        forever for a request that actually never completed."""
        with self.db.connect() as connection:
            connection.execute(
                """DELETE FROM idempotent_requests
                   WHERE user_id = ? AND idempotency_key = ? AND response_json IS NULL""",
                (user_id, key))

    def _prune(self) -> None:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=self.ttl_hours)).strftime(_DT_FORMAT)
        with self.db.connect() as connection:
            connection.execute('DELETE FROM idempotent_requests WHERE created_at < ?', (cutoff,))
