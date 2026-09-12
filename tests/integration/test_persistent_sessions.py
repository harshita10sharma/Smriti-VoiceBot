"""Session ownership and pending-confirmation state must survive a process
restart. These tests build two independent SessionStore/MemoryRepository
instances against the *same on-disk database file* -- not the same Python
objects -- to prove state genuinely round-trips through SQLite rather than
merely surviving because it's still the same in-memory object.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest

from smriti_voice.conversation.context import SessionStore
from smriti_voice.database import Database
from smriti_voice.memory.repository import MemoryRepository
from smriti_voice.schemas import PendingConfirmation


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / 'sessions.db')


def _store(db_path: str, **kwargs) -> SessionStore:
    """A fresh SessionStore over a fresh MemoryRepository/Database -- the
    closest thing to "a new process" this test suite can build without an
    actual subprocess, since neither object is shared with any other
    store built against the same path."""
    repo = MemoryRepository(Database(db_path))
    return SessionStore(repo, **kwargs)


# --------------------------------------------------------------------------- #
# Basic round-trip across independent store instances
# --------------------------------------------------------------------------- #
def test_session_created_by_one_store_is_visible_to_a_new_store_instance(db_path):
    first_store = _store(db_path)
    session = first_store.get_or_create(None, 'patient-a', 'eng')

    second_store = _store(db_path)
    reloaded = second_store.get_or_create(session.session_id, 'patient-a', 'eng')

    assert reloaded.session_id == session.session_id
    assert reloaded.user_id == 'patient-a'
    assert reloaded.language == 'eng'


def test_ownership_survives_across_store_instances(db_path):
    first_store = _store(db_path)
    session = first_store.get_or_create(None, 'patient-a', 'eng')

    second_store = _store(db_path)
    with pytest.raises(PermissionError):
        second_store.get_or_create(session.session_id, 'patient-b', 'eng')


# --------------------------------------------------------------------------- #
# Pending confirmation survives a restart, bound to patient + session + action
# --------------------------------------------------------------------------- #
def test_pending_confirmation_survives_a_restart(db_path):
    first_store = _store(db_path)
    session = first_store.get_or_create(None, 'patient-a', 'eng')
    pending = PendingConfirmation(action='CALL_PRIMARY_CONTACT', tool_name='call_family_member',
                                  arguments={'name': 'Bina'}, prompt='Shall I call Bina?',
                                  language='eng')
    session.set_pending(pending)
    first_store.save(session)

    second_store = _store(db_path)
    reloaded = second_store.get_or_create(session.session_id, 'patient-a', 'eng')

    assert reloaded.pending is not None
    assert reloaded.pending.action == 'CALL_PRIMARY_CONTACT'
    assert reloaded.pending.tool_name == 'call_family_member'
    assert reloaded.pending.arguments == {'name': 'Bina'}


def test_clearing_a_pending_confirmation_persists_across_restart(db_path):
    first_store = _store(db_path)
    session = first_store.get_or_create(None, 'patient-a', 'eng')
    session.set_pending(PendingConfirmation(action='CALL_PRIMARY_CONTACT', language='eng'))
    first_store.save(session)

    # Resolved (confirmed or cancelled) in the same "process".
    session.set_pending(None)
    first_store.save(session)

    second_store = _store(db_path)
    reloaded = second_store.get_or_create(session.session_id, 'patient-a', 'eng')
    assert reloaded.pending is None  # never resurrected as pending after restart


def test_full_confirmation_flow_survives_a_restart_via_real_application(db_path, monkeypatch):
    """End-to-end through ConversationManager.handle(), with a genuine
    Application rebuild (a second, independent Application.build() call
    against the same database file) standing in for a process restart."""
    from smriti_voice.app import Application
    from smriti_voice.config import AppConfig
    from smriti_voice.memory.models import FamilyMember, User
    from test_conversation_scenarios import tool_then_answer, use_mock_llm

    monkeypatch.setenv('SMRITI_TTS_PROVIDER', 'mock')
    monkeypatch.setenv('SMRITI_LLM_PROVIDER', 'mock')
    monkeypatch.delenv('SARVAM_API_KEY', raising=False)
    monkeypatch.delenv('GEMINI_API_KEY', raising=False)
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)

    app1 = Application.build(AppConfig.load(), database=Database(db_path))
    app1.memory.repo.upsert_user(User(user_id='patient-a', display_name='Patient A'))
    app1.memory.repo.add_family_member(FamilyMember(
        user_id='patient-a', name='Bina', relation='daughter', phone='+919000000001',
        is_trusted_contact=True))
    use_mock_llm(app1, script=[tool_then_answer('call_family_member', 'x', name='Bina')])

    first = app1.conversation.handle(user_id='patient-a', message='Call Bina', language='eng')
    assert first.requires_confirmation is True

    # "Restart": a second, fully independent Application built from scratch
    # against the same on-disk database.
    app2 = Application.build(AppConfig.load(), database=Database(db_path))
    second = app2.conversation.handle(user_id='patient-a', message='Yes',
                                      session_id=first.session_id, language='eng')

    assert second.action == 'CALL_PRIMARY_CONTACT'
    assert second.action_accepted is True


# --------------------------------------------------------------------------- #
# Expiry: an expired session is never resurrected under the same id
# --------------------------------------------------------------------------- #
def test_expired_session_is_not_resurrected_under_the_same_id(db_path):
    store = _store(db_path, idle_timeout_minutes=30)
    session = store.get_or_create(None, 'patient-a', 'eng')

    # Force it to look old by rewriting last_active_at directly.
    stale = (datetime.now(timezone.utc) - timedelta(minutes=60)).strftime('%Y-%m-%d %H:%M:%S')
    with store.repo.db.connect() as connection:
        connection.execute('UPDATE conversations SET last_active_at = ? WHERE session_id = ?',
                           (stale, session.session_id))

    fresh = store.get_or_create(session.session_id, 'patient-a', 'eng')
    assert fresh.session_id != session.session_id  # a new id, not a resurrected old one


def test_expired_pending_confirmation_is_never_auto_approved_after_restart(db_path):
    """expire_pending() ages out a pending confirmation older than 3
    minutes; this must hold true even when the session was just reloaded
    from a "different process"."""
    store = _store(db_path)
    session = store.get_or_create(None, 'patient-a', 'eng')
    old_pending = PendingConfirmation(
        action='CALL_PRIMARY_CONTACT', language='eng',
        created_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    session.set_pending(old_pending)
    store.save(session)

    reloaded_store = _store(db_path)
    reloaded = reloaded_store.get_or_create(session.session_id, 'patient-a', 'eng')
    assert reloaded.pending is not None  # reloaded as-is, still there
    reloaded.expire_pending()
    assert reloaded.pending is None  # but expires on use, exactly as before a restart


# --------------------------------------------------------------------------- #
# exists()/drop() work against persisted state
# --------------------------------------------------------------------------- #
def test_exists_reflects_persisted_state_across_stores(db_path):
    first_store = _store(db_path)
    session = first_store.get_or_create(None, 'patient-a', 'eng')

    second_store = _store(db_path)
    assert second_store.exists(session.session_id) is True
    assert second_store.exists('no-such-session') is False


def test_drop_removes_the_persisted_session(db_path):
    store = _store(db_path)
    session = store.get_or_create(None, 'patient-a', 'eng')
    store.drop(session.session_id)
    assert store.exists(session.session_id) is False
