"""Conversation session state.

History is bounded in two ways: a turn count and a maximum age.  Nothing sends
an unbounded transcript to a provider, and an abandoned session expires rather
than lingering with a pending confirmation that could later be answered "yes".
"""
from __future__ import annotations

import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Deque

from ..schemas import ConversationTurn, PendingConfirmation, TurnKind, ToolResult


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


class SessionStore:
    """In-memory sessions with capacity and idle eviction.

    Durable history lives in SQLite (``conversation_turns``); this store only
    holds the live working state, so a restart loses nothing that matters.
    """

    def __init__(self, *, max_turns: int = 8, idle_timeout_minutes: int = 30,
                 max_sessions: int = 500) -> None:
        self.max_turns = max_turns
        self.idle_timeout_minutes = idle_timeout_minutes
        self.max_sessions = max_sessions
        self._sessions: dict[str, ConversationSession] = {}

    def get_or_create(self, session_id: str | None, user_id: str,
                      language: str = 'eng') -> ConversationSession:
        self.evict_expired()
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
            # A session belongs to exactly one user; never hand it to another.
            if session.user_id != user_id:
                raise PermissionError('session does not belong to this user')
            if session.is_expired():
                del self._sessions[session_id]
            else:
                session.touch()
                return session
        session = ConversationSession(session_id=session_id or new_session_id(),
                                      user_id=user_id, language=language,
                                      max_turns=self.max_turns,
                                      idle_timeout_minutes=self.idle_timeout_minutes)
        if len(self._sessions) >= self.max_sessions:
            oldest = min(self._sessions.values(), key=lambda s: s.last_active_at)
            self._sessions.pop(oldest.session_id, None)
        self._sessions[session.session_id] = session
        return session

    def evict_expired(self) -> int:
        expired = [sid for sid, session in self._sessions.items() if session.is_expired()]
        for sid in expired:
            del self._sessions[sid]
        return len(expired)

    def drop(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def exists(self, session_id: str) -> bool:
        """True if this exact session_id is currently tracked (not expired,
        not evicted). Used to report whether a welcome/session-init call
        restored an existing session or started a fresh one."""
        return session_id in self._sessions

    def __len__(self) -> int:
        return len(self._sessions)
