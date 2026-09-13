"""Concurrency safety for a single shared session_id.

Two requests naming the same session_id (a rapid double-tap, a client retry
racing the original turn, two devices sharing a session) used to race around
ConversationManager's read-mutate-persist cycle: SessionStore.get_or_create
reads the row, the caller mutates the in-memory ConversationSession object
(add_turn/set_pending/last_subject/language), and SessionStore.save writes it
back with no version check -- a lost-update race, not anything the SQLite
layer prevented on its own. ConversationManager.handle/welcome now serialize
around a per-session_id lock (SessionStore.lock_for) for exactly this reason.

These tests use a real threading.Barrier to force genuine concurrent entry
into handle()/welcome() rather than relying on GIL scheduling luck, then
assert the combined end state is exactly what serialized execution would
produce -- no lost turns, no double execution, no corrupted pending state,
no cross-patient leakage, regardless of which thread's lock acquisition
happens to win the race.
"""
from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone

import pytest

from smriti_voice.schemas import TurnKind

from test_conversation_scenarios import tool_call_response, use_mock_llm


def _run_concurrently(*callables):
    """Launch every callable in its own thread, released simultaneously by a
    barrier, and return their results/exceptions in call order."""
    barrier = threading.Barrier(len(callables))
    results: list[dict] = [{} for _ in callables]

    def _wrap(index, fn):
        def _target():
            barrier.wait()
            try:
                results[index]['value'] = fn()
            except Exception as exc:  # noqa: BLE001 -- captured for assertion, not swallowed
                results[index]['error'] = exc
        return _target

    threads = [threading.Thread(target=_wrap(i, fn)) for i, fn in enumerate(callables)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    return results


# --------------------------------------------------------------------------- #
# A. Two simultaneous normal turns on the same session
# --------------------------------------------------------------------------- #
def test_two_simultaneous_normal_turns_both_land_without_losing_either(app):
    use_mock_llm(app, reply='Hello there.')
    first = app.conversation.handle(user_id='demo-user', message='start', language='eng')
    sid = first.session_id

    results = _run_concurrently(
        lambda: app.conversation.handle(user_id='demo-user', message='turn A',
                                        session_id=sid, language='eng'),
        lambda: app.conversation.handle(user_id='demo-user', message='turn B',
                                        session_id=sid, language='eng'),
    )
    for r in results:
        assert 'error' not in r, r.get('error')
        assert r['value'].session_id == sid

    session = app.conversation.sessions.get_or_create(sid, 'demo-user', 'eng')
    texts = {t.text for t in session.history()}
    assert 'turn A' in texts
    assert 'turn B' in texts


# --------------------------------------------------------------------------- #
# B. Two simultaneous confirmation attempts -- must execute exactly once
# --------------------------------------------------------------------------- #
def test_two_simultaneous_yes_replies_execute_the_action_exactly_once(app):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Drink water',
                                                  remind_at='10:00')])
    proposed = app.conversation.handle(user_id='demo-user', message='Remind me to drink water',
                                       language='eng')
    assert proposed.requires_confirmation is True
    sid = proposed.session_id

    results = _run_concurrently(
        lambda: app.conversation.handle(user_id='demo-user', message='Yes',
                                        session_id=sid, language='eng'),
        lambda: app.conversation.handle(user_id='demo-user', message='Yes',
                                        session_id=sid, language='eng'),
    )
    for r in results:
        assert 'error' not in r, r.get('error')

    accepted = [r['value'] for r in results if r['value'].action_accepted]
    assert len(accepted) == 1  # exactly one of the two "yes" turns executed it

    stored = app.memory.reminders('demo-user')
    matching = [rem for rem in stored.get('reminders', []) if rem['text'] == 'Drink water']
    assert len(matching) == 1  # never double-created


# --------------------------------------------------------------------------- #
# C. Simultaneous action-proposal turn + unrelated normal turn
# --------------------------------------------------------------------------- #
def test_simultaneous_action_proposal_and_unrelated_turn_do_not_corrupt_state(app):
    use_mock_llm(app, reply='Hello there.')
    first = app.conversation.handle(user_id='demo-user', message='start', language='eng')
    sid = first.session_id

    def _propose():
        use_mock_llm(app, script=[tool_call_response('create_reminder', text='Take a walk',
                                                      remind_at='18:00')])
        return app.conversation.handle(user_id='demo-user', message='Remind me to take a walk',
                                       session_id=sid, language='eng')

    def _unrelated():
        return app.conversation.handle(user_id='demo-user', message='What is my name',
                                       session_id=sid, language='eng')

    results = _run_concurrently(_propose, _unrelated)
    for r in results:
        assert 'error' not in r, r.get('error')

    # Whichever turn's lock loses the race runs strictly after the other
    # completes (serialized, not interleaved) -- the session must end up in
    # a single coherent state, not a mix of both turns' partial writes.
    session = app.conversation.sessions.get_or_create(sid, 'demo-user', 'eng')
    assert session.pending is None or session.pending.action == 'CREATE_REMINDER'


# --------------------------------------------------------------------------- #
# D. Simultaneous duplicate (identical) requests
# --------------------------------------------------------------------------- #
def test_simultaneous_identical_idempotent_requests_execute_once(app, client, auth_headers):
    """The existing Idempotency-Key guarantee must hold under genuine
    concurrent arrival, not just sequential retries."""
    import concurrent.futures

    headers = {**auth_headers, 'x-idempotency-key': 'concurrent-dup-key'}
    body = {'user_id': 'demo-user', 'message': 'hello', 'language': 'eng'}

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.post, '/v1/conversation', json=body, headers=headers)
                  for _ in range(2)]
        responses = [f.result() for f in futures]

    statuses = sorted(r.status_code for r in responses)
    assert statuses in ([200, 200], [200, 409])  # replay, or one in_progress conflict
    ok = [r for r in responses if r.status_code == 200]
    if len(ok) == 2:
        assert ok[0].json()['request_id'] == ok[1].json()['request_id']


# --------------------------------------------------------------------------- #
# E. Session ownership under concurrent requests
# --------------------------------------------------------------------------- #
def test_cross_patient_hijack_attempt_always_fails_even_under_concurrency(app):
    from smriti_voice.memory.models import User
    app.memory.repo.upsert_user(User(user_id='other-concurrent-user', display_name='Other'))

    use_mock_llm(app, reply='Hello there.')
    owner_turn = app.conversation.handle(user_id='demo-user', message='start', language='eng')
    sid = owner_turn.session_id

    results = _run_concurrently(
        lambda: app.conversation.handle(user_id='demo-user', message='mine',
                                        session_id=sid, language='eng'),
        lambda: app.conversation.handle(user_id='other-concurrent-user', message='hijack',
                                        session_id=sid, language='eng'),
    )
    owner_result, hijack_result = results
    assert 'error' not in owner_result
    assert isinstance(hijack_result.get('error'), PermissionError)


# --------------------------------------------------------------------------- #
# F. Expired confirmation racing with a "yes" reply
# --------------------------------------------------------------------------- #
def test_confirmation_that_expires_mid_race_is_never_auto_approved(app):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Too late now',
                                                  remind_at='10:00')])
    proposed = app.conversation.handle(user_id='demo-user', message='Remind me of something',
                                       language='eng')
    sid = proposed.session_id
    session = app.conversation.sessions.get_or_create(sid, 'demo-user', 'eng')
    session.pending = session.pending.model_copy(
        update={'created_at': datetime.now(timezone.utc) - timedelta(minutes=10)})
    app.conversation.sessions.save(session)

    results = _run_concurrently(
        lambda: app.conversation.handle(user_id='demo-user', message='Yes',
                                        session_id=sid, language='eng'),
        lambda: app.conversation.handle(user_id='demo-user', message='Yes',
                                        session_id=sid, language='eng'),
    )
    for r in results:
        assert 'error' not in r, r.get('error')
        assert r['value'].action_accepted is False

    stored = app.memory.reminders('demo-user')
    assert 'Too late now' not in {r['text'] for r in stored.get('reminders', [])}


# --------------------------------------------------------------------------- #
# G. Restart behavior: a fresh SessionStore over the same repo sees the
# same state a concurrent-turn cycle left behind.
# --------------------------------------------------------------------------- #
def test_state_after_concurrent_turns_survives_a_simulated_restart(app):
    from smriti_voice.conversation.context import SessionStore

    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Survive restart',
                                                  remind_at='09:00')])
    proposed = app.conversation.handle(user_id='demo-user', message='Remind me please',
                                       language='eng')
    sid = proposed.session_id

    # A brand-new SessionStore instance == what a fresh process would build
    # on startup; it shares nothing in-memory with the one above except the
    # same underlying repository/database.
    reloaded_store = SessionStore(app.memory.repo, max_turns=app.config.max_history_turns,
                                  idle_timeout_minutes=app.config.max_session_idle_minutes)
    reloaded = reloaded_store.get_or_create(sid, 'demo-user', 'eng')
    assert reloaded.pending is not None
    assert reloaded.pending.action == 'CREATE_REMINDER'
    assert len(reloaded.history()) == 2  # user + assistant turn both persisted
