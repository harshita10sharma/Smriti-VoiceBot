"""ConversationManager.welcome() and POST /v1/conversation/welcome.

Proves the specific guarantees required of a proactive first message: no
fake user turn, no LLM call, no persisted history, idempotent repeated
initialization, session restoration, and that the user's first real message
afterwards is completely unaffected.
"""
from __future__ import annotations

from smriti_voice.schemas import TurnKind

from test_conversation_scenarios import use_mock_llm


# --------------------------------------------------------------------------- #
# ConversationManager.welcome() directly
# --------------------------------------------------------------------------- #
def test_welcome_creates_a_new_session_and_greets(app):
    outcome = app.conversation.welcome(user_id='demo-user', language='eng')
    assert outcome.session_id
    assert outcome.restored is False
    assert 'Hello' in outcome.text
    assert outcome.language == 'eng'


def test_welcome_restores_an_existing_session(app):
    first = app.conversation.welcome(user_id='demo-user', language='eng')
    second = app.conversation.welcome(user_id='demo-user', session_id=first.session_id,
                                      language='eng')
    assert second.session_id == first.session_id
    assert second.restored is True


def test_welcome_never_calls_the_model(app):
    provider = use_mock_llm(app, reply='the model must never be called for a welcome')
    app.conversation.welcome(user_id='demo-user', language='eng')
    assert provider.calls == []


def test_welcome_never_writes_conversation_history(app):
    outcome = app.conversation.welcome(user_id='demo-user', language='eng')
    # No turn was persisted: recent_turns for this brand-new session is empty.
    turns = app.memory.repo.recent_turns(outcome.session_id, 'demo-user', limit=10)
    assert turns == []


def test_welcome_does_not_consume_the_users_first_real_message(app):
    """The scenario the whole feature exists for: open the app, get a
    welcome, then say something real -- and have that first real message
    behave exactly as if welcome() had never been called."""
    provider = use_mock_llm(app, reply='Your daughter is called Bina.')
    outcome = app.conversation.welcome(user_id='demo-user', language='eng')

    reply = app.conversation.handle(user_id='demo-user', message="What is my daughter's name?",
                                    session_id=outcome.session_id, language='eng')
    assert reply.kind is not TurnKind.WELCOME
    assert len(provider.calls) == 1  # exactly the real turn, nothing extra
    # The real turn is genuinely turn #1 in history, not #2 after a phantom
    # welcome turn.
    turns = app.memory.repo.recent_turns(reply.session_id, 'demo-user', limit=10)
    assert len(turns) == 2  # one user turn, one assistant turn
    assert turns[0]['role'] == 'user'
    assert turns[0]['text'] == "What is my daughter's name?"


def test_repeated_welcome_is_idempotent_and_side_effect_free(app):
    first = app.conversation.welcome(user_id='demo-user', language='eng')
    second = app.conversation.welcome(user_id='demo-user', session_id=first.session_id,
                                      language='eng')
    third = app.conversation.welcome(user_id='demo-user', session_id=first.session_id,
                                     language='eng')
    assert first.text == second.text == third.text
    assert second.restored is True and third.restored is True


def test_welcome_cannot_be_used_to_read_another_users_session(app, two_users):
    mine = app.conversation.welcome(user_id='demo-user', language='eng')
    import pytest
    with pytest.raises(PermissionError):
        app.conversation.welcome(user_id='other-user', session_id=mine.session_id, language='eng')


# --------------------------------------------------------------------------- #
# HTTP layer
# --------------------------------------------------------------------------- #
def test_welcome_endpoint_text_only(client, auth_headers):
    resp = client.post('/v1/conversation/welcome',
                       json={'user_id': 'demo-user', 'language': 'eng'},
                       headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body['kind'] == 'WELCOME'
    assert body['session_restored'] is False
    assert body['job_id'] is None
    assert body['job_status'] == 'NOT_REQUESTED'
    assert 'Hello' in body['response_text']


def test_welcome_endpoint_restores_session(client, auth_headers):
    first = client.post('/v1/conversation/welcome', json={'user_id': 'demo-user'},
                        headers=auth_headers).json()
    second = client.post('/v1/conversation/welcome',
                         json={'user_id': 'demo-user', 'session_id': first['session_id']},
                         headers=auth_headers).json()
    assert second['session_id'] == first['session_id']
    assert second['session_restored'] is True


def test_welcome_endpoint_with_speak_queues_a_tts_job(client, auth_headers):
    resp = client.post('/v1/conversation/welcome',
                       json={'user_id': 'demo-user', 'language': 'eng', 'speak': True},
                       headers=auth_headers)
    body = resp.json()
    assert body['job_id'] is not None
    assert body['job_status'] == 'QUEUED'


def test_welcome_endpoint_rejects_unauthorized_user(client, auth_headers):
    resp = client.post('/v1/conversation/welcome', json={'user_id': 'someone-else'},
                       headers=auth_headers)
    assert resp.status_code == 403


def test_welcome_endpoint_requires_auth(client):
    resp = client.post('/v1/conversation/welcome', json={'user_id': 'demo-user'})
    assert resp.status_code == 401
