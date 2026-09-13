"""Conversation session state.

History is bounded in two ways: a turn count and a maximum age.  Nothing sends
an unbounded transcript to a provider, and an abandoned session expires rather
than lingering with a pending confirmation that could later be answered "yes".
"""
from __future__ import annotations

import threading
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Deque

from ..schemas import ConversationTurn, PendingConfirmation, TurnKind, ToolResult

if TYPE_CHECKING:
    from ..memory.repository import MemoryRepository


def _now() -> datetime:
    return datetime.now(timezone.utc)


def new_session_id() -> str:
    return uuid.uuid4().hex


@dataclass
class ConversationSession:
    session_id: str
    user_id: str
    language: str = 'eng'
    max_turns: int = 8
    idle_timeout_minutes: int = 30
    turns: Deque[ConversationTurn] = field(default_factory=deque)
    pending: PendingConfirmation | None = None
    last_tool_results: list[ToolResult] = field(default_factory=list)
    last_active_at: datetime = field(default_factory=_now)
    created_at: datetime = field(default_factory=_now)
    # Names mentioned in the last assistant turn, so "she"/"he" can be resolved.
    last_subject: str | None = None

    # ------------------------------------------------------------------ #
    def is_expired(self, *, now: datetime | None = None) -> bool:
        reference = now or _now()
        return (reference - self.last_active_at) > timedelta(minutes=self.idle_timeout_minutes)

    def touch(self) -> None:
        self.last_active_at = _now()

    def add_turn(self, role: str, text: str, *, language: str | None = None,
                 kind: TurnKind = TurnKind.CONVERSATION) -> ConversationTurn:
        turn = ConversationTurn(turn_id=uuid.uuid4().hex, session_id=self.session_id,
                                user_id=self.user_id, role=role, text=text,
                                language=language or self.language, kind=kind)
        self.turns.append(turn)
        while len(self.turns) > self.max_turns * 2:  # user+assistant pairs
            self.turns.popleft()
        self.touch()
        return turn

    def history(self, limit: int | None = None) -> list[ConversationTurn]:
        turns = list(self.turns)
        if limit:
            turns = turns[-limit:]
        return turns

    # ------------------------------------------------------------------ #
    def set_pending(self, pending: PendingConfirmation | None) -> None:
        self.pending = pending

    def expire_pending(self) -> None:
        """A confirmation only stands for a couple of turns."""
        if self.pending is None:
            return
        age = _now() - self.pending.created_at
        if age > timedelta(minutes=3):
            self.pending = None


def _safe_turn_kind(raw: str) -> TurnKind:
    """conversation_turns.kind stores the literal 'USER' for a user turn
    (see ConversationManager._persist) alongside real TurnKind values for
    assistant turns -- 'USER' is not itself a TurnKind member. Reconstructed
    history only ever reads ``turn.role``/``turn.text`` (see
    ConversationManager._history_messages), never ``turn.kind``, so any
    unrecognized stored value safely falls back to CONVERSATION rather than
    raising."""
    try:
        return TurnKind(raw)
    except ValueError:
        return TurnKind.CONVERSATION


def _parse_db_dt(value: str) -> datetime:
    """SQLite's ``datetime('now')`` produces 'YYYY-MM-DD HH:MM:SS', UTC,
    with no timezone marker. Attach one so comparisons against ``_now()``
    (timezone-aware) work correctly."""
    return datetime.strptime(value, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)


class SessionStore:
    """Patient-scoped sessions, persisted in SQLite via ``MemoryRepository``.

    Ownership, idle-timeout state, the pending confirmation and the last
    conversational subject (for pronoun resolution) all survive a process
    restart: a session reloaded after a restart looks exactly like it did
    before, rather than silently vanishing mid-confirmation. Bounded
    history is reconstructed from ``conversation_turns`` -- the existing
    durable turn record -- so nothing here duplicates that table.

    Call :meth:`save` once a turn has decided the session's new pending
    confirmation / last-subject / language; ``get_or_create`` and the
    session's own in-memory mutators (``add_turn``, ``set_pending``, ...)
    do not write through automatically, exactly like the prior in-memory
    version did not need to.
    """

    def __init__(self, repository: 'MemoryRepository', *, max_turns: int = 8,
                 idle_timeout_minutes: int = 30) -> None:
        self.repo = repository
        self.max_turns = max_turns
        self.idle_timeout_minutes = idle_timeout_minutes
        # Per-session locks: two requests naming the same session_id (a
        # rapid double-tap, a client retry racing the original, two
        # devices sharing a session) must serialize around the
        # read-mutate-persist cycle in ConversationManager.handle/welcome,
        # or whichever save() lands last silently overwrites the other's
        # pending confirmation / last_subject / language -- a lost-update
        # race, not anything the SQLite layer itself prevents on its own.
        # A single in-process lock per session_id is correct and sufficient
        # for the documented single-worker deployment (see SECURITY.md);
        # it does not protect a future multi-process/multi-worker
        # deployment, which would need a database-level lock instead.
        self._locks: dict[str, threading.Lock] = {}
        self._locks_guard = threading.Lock()

    def lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            lock = self._locks.get(session_id)
            if lock is None:
                lock = threading.Lock()
                self._locks[session_id] = lock
            return lock

    def get_or_create(self, session_id: str | None, user_id: str,
                      language: str = 'eng') -> ConversationSession:
        if session_id:
            row = self.repo.get_session(session_id)
            if row is not None:
                # A session belongs to exactly one user; never hand it to another.
                if row['user_id'] != user_id:
                    raise PermissionError('session does not belong to this user')
                session = self._from_row(row)
                if not session.is_expired():
                    return session
                # Expired: never resurrect it under the same id (a stale
                # pending confirmation must never come back to life) --
                # a fresh session_id starts clean, exactly like the old
                # in-memory store deleting and recreating did.
        return self._create(user_id, language)

    def _create(self, user_id: str, language: str) -> ConversationSession:
        session_id = new_session_id()
        self.repo.save_session_state(session_id, user_id, language,
                                     pending_json=None, last_subject=None)
        return ConversationSession(session_id=session_id, user_id=user_id, language=language,
                                   max_turns=self.max_turns,
                                   idle_timeout_minutes=self.idle_timeout_minutes)

    def _from_row(self, row) -> ConversationSession:
        keys = row.keys()
        raw_turns = self.repo.recent_turns(row['session_id'], row['user_id'],
                                           limit=self.max_turns * 2)
        turns: Deque[ConversationTurn] = deque(
            ConversationTurn(turn_id=t['turn_id'], session_id=t['session_id'],
                             user_id=t['user_id'], role=t['role'], text=t['text'],
                             language=t['language'], kind=_safe_turn_kind(t['kind']))
            for t in raw_turns)
        pending_json = row['pending_json'] if 'pending_json' in keys else None
        pending = PendingConfirmation.model_validate_json(pending_json) if pending_json else None
        return ConversationSession(
            session_id=row['session_id'], user_id=row['user_id'], language=row['language'],
            max_turns=self.max_turns, idle_timeout_minutes=self.idle_timeout_minutes,
            turns=turns, pending=pending,
            last_subject=row['last_subject'] if 'last_subject' in keys else None,
            last_active_at=_parse_db_dt(row['last_active_at']),
            created_at=_parse_db_dt(row['started_at']))

    def save(self, session: ConversationSession) -> None:
        """Persist the live state a turn may have changed: pending
        confirmation, last subject, language. Call this once, after
        routing has finished mutating the session for this turn."""
        self.repo.save_session_state(
            session.session_id, session.user_id, session.language,
            pending_json=session.pending.model_dump_json() if session.pending else None,
            last_subject=session.last_subject)

    def drop(self, session_id: str) -> None:
        self.repo.delete_session(session_id)

    def exists(self, session_id: str) -> bool:
        """True if this exact session_id is currently tracked and not
        expired. Used to report whether a welcome/session-init call
        restored an existing session or started a fresh one."""
        row = self.repo.get_session(session_id)
        if row is None:
            return False
        age = _now() - _parse_db_dt(row['last_active_at'])
        return age <= timedelta(minutes=self.idle_timeout_minutes)
