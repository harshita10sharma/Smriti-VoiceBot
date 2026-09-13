"""The existing architecture already implements a deterministic action
state machine -- proposal -> schema validation -> safety -> authorization
-> confirmation -> executor -> result (see ToolRegistry.execute and
ConversationManager._resolve_confirmation) -- it just isn't reified as a
named enum. These tests pin each state transition explicitly so a future
change can't silently blur "the model proposed this" with "this actually
happened," and prove that confirming twice (e.g. a client retry) never
executes a controlled action twice.
"""
from __future__ import annotations

from smriti_voice.schemas import TurnKind

from test_conversation_scenarios import tool_call_response, tool_then_answer, use_mock_llm


# --------------------------------------------------------------------------- #
# PROPOSED -> AWAITING_CONFIRMATION: nothing executes yet
# --------------------------------------------------------------------------- #
def test_proposing_a_controlled_action_does_not_execute_it(app):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Take a walk',
                                                  remind_at='18:00')])
    reply = app.conversation.handle(user_id='demo-user', message='Remind me to take a walk',
                                    language='eng')
    assert reply.requires_confirmation is True
    assert reply.action_accepted is False
    assert reply.kind is TurnKind.CONFIRMATION
    stored = app.memory.reminders('demo-user')
    assert 'Take a walk' not in {r['text'] for r in stored.get('reminders', [])}


# --------------------------------------------------------------------------- #
# AWAITING_CONFIRMATION -> COMPLETED (explicit yes)
# --------------------------------------------------------------------------- #
def test_confirming_executes_and_reports_action_accepted(app):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Drink water',
                                                  remind_at='10:00')])
    first = app.conversation.handle(user_id='demo-user', message='Remind me to drink water',
                                    language='eng')
    assert first.requires_confirmation is True

    second = app.conversation.handle(user_id='demo-user', message='Yes',
                                     session_id=first.session_id, language='eng')
    assert second.action == 'CREATE_REMINDER'
    assert second.action_accepted is True
    stored = app.memory.reminders('demo-user')
    assert 'Drink water' in {r['text'] for r in stored.get('reminders', [])}


# --------------------------------------------------------------------------- #
# AWAITING_CONFIRMATION -> CANCELLED (explicit no)
# --------------------------------------------------------------------------- #
def test_declining_cancels_and_never_executes(app):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Never happens',
                                                  remind_at='10:00')])
    first = app.conversation.handle(user_id='demo-user', message='Remind me of something',
                                    language='eng')
    assert first.requires_confirmation is True

    second = app.conversation.handle(user_id='demo-user', message='No',
                                     session_id=first.session_id, language='eng')
    assert second.action_accepted is False
    assert second.kind is TurnKind.CONFIRMATION
    stored = app.memory.reminders('demo-user')
    assert 'Never happens' not in {r['text'] for r in stored.get('reminders', [])}

    # And a later, unrelated "yes" in the same session does not retroactively
    # execute the cancelled action -- there is nothing pending any more.
    third = app.conversation.handle(user_id='demo-user', message='Yes',
                                    session_id=first.session_id, language='eng')
    assert third.action_accepted is False


# --------------------------------------------------------------------------- #
# AWAITING_CONFIRMATION -> EXPIRED: never auto-approved
# --------------------------------------------------------------------------- #
def test_expired_pending_confirmation_is_never_auto_approved(app):
    from datetime import datetime, timedelta, timezone
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Too late now',
                                                  remind_at='10:00')])
    first = app.conversation.handle(user_id='demo-user', message='Remind me of something',
                                    language='eng')
    session = app.conversation.sessions.get_or_create(first.session_id, 'demo-user', 'eng')
    assert session.pending is not None
    session.pending = session.pending.model_copy(
        update={'created_at': datetime.now(timezone.utc) - timedelta(minutes=10)})
    app.conversation.sessions.save(session)

    second = app.conversation.handle(user_id='demo-user', message='Yes',
                                     session_id=first.session_id, language='eng')
    assert second.action_accepted is False
    stored = app.memory.reminders('demo-user')
    assert 'Too late now' not in {r['text'] for r in stored.get('reminders', [])}


# --------------------------------------------------------------------------- #
# Unknown actions fail closed
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# action_id: a stable correlator for one specific proposal instance
# --------------------------------------------------------------------------- #
def test_action_id_is_present_and_stable_across_propose_and_confirm(app):
    """metadata.action_id lets a client correlate the exact proposal a later
    confirmation resolves, rather than relying on session_id implying 'the
    one pending action' -- additive, does not change any existing field."""
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Take a walk',
                                                  remind_at='18:00')])
    proposed = app.conversation.handle(user_id='demo-user', message='Remind me to take a walk',
                                       language='eng')
    assert proposed.metadata.action_id is not None

    confirmed = app.conversation.handle(user_id='demo-user', message='Yes',
                                        session_id=proposed.session_id, language='eng')
    assert confirmed.metadata.action_id == proposed.metadata.action_id


def test_action_id_is_absent_for_a_turn_with_no_controlled_action(app):
    use_mock_llm(app, reply='Hello there.')
    reply = app.conversation.handle(user_id='demo-user', message='hello', language='eng')
    assert reply.metadata.action_id is None


def test_unknown_tool_name_fails_closed(app):
    use_mock_llm(app, script=[tool_call_response('delete_everything', target='all')])
    reply = app.conversation.handle(user_id='demo-user', message='Do something dangerous',
                                    language='eng')
    assert reply.action_accepted is False
    assert not any(r.executed for r in reply.tool_results)


def test_sensitive_tool_never_executes_even_if_the_model_calls_it(app):
    use_mock_llm(app, script=[tool_call_response('change_medication', name='X', dose='999mg')])
    reply = app.conversation.handle(user_id='demo-user', message='Change my medicine dose',
                                    language='eng')
    assert reply.action_accepted is False
    assert not any(r.executed for r in reply.tool_results)


# --------------------------------------------------------------------------- #
# Idempotent confirmation: a retried "yes" (same Idempotency-Key) must not
# execute the controlled action twice.
# --------------------------------------------------------------------------- #
def test_retried_confirmation_with_same_idempotency_key_executes_once(app, client, auth_headers):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Once only',
                                                  remind_at='09:00')])
    first = client.post('/v1/conversation',
                        json={'user_id': 'demo-user', 'message': 'Remind me', 'language': 'eng'},
                        headers=auth_headers).json()
    assert first['requires_confirmation'] is True

    headers = {**auth_headers, 'x-idempotency-key': 'confirm-once'}
    body = {'user_id': 'demo-user', 'message': 'Yes', 'session_id': first['session_id'],
           'language': 'eng'}
    confirm_a = client.post('/v1/conversation', json=body, headers=headers)
    confirm_b = client.post('/v1/conversation', json=body, headers=headers)

    assert confirm_a.status_code == confirm_b.status_code == 200
    assert confirm_a.json()['request_id'] == confirm_b.json()['request_id']

    stored = app.memory.reminders('demo-user')
    matching = [r for r in stored.get('reminders', []) if r['text'] == 'Once only']
    assert len(matching) == 1  # created exactly once, not twice
