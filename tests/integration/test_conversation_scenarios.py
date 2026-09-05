"""The validation scenarios from the specification, run against the real stack.

The LLM is mocked (no credentials and no egress in CI), but everything else is
real: the safety layer, the command router, the tool registry, SQLite and the
language routing. Each test states which part is mocked.
"""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from smriti_voice.llm.base import Message
from smriti_voice.llm.mock import MockLLMProvider, tool_call_response
from smriti_voice.schemas import LLMResponse, TurnKind


def use_mock_llm(app, *, script=None, handler=None, reply='I am here to help you.'):
    """Install a scripted model.  Returns it so calls can be inspected."""
    provider = MockLLMProvider(script=script, handler=handler, reply=reply)
    app.llm._cache['mock'] = provider
    return provider


def tool_then_answer(tool_name, answer, **arguments):
    return [tool_call_response(tool_name, **arguments), LLMResponse(text=answer)]


# --------------------------------------------------------------------------- #
# TEST 5 / 6 — personal memory must come from the database
# --------------------------------------------------------------------------- #
def test_daughter_name_comes_from_a_tool_not_the_model(app):
    provider = use_mock_llm(app, script=tool_then_answer(
        'get_family_member', 'Your daughter is called Bina. She lives in Shillong.',
        relation='daughter'))
    reply = app.conversation.handle(user_id='demo-user',
                                    message="What is my daughter's name?", language='eng')
    assert reply.kind is TurnKind.MEMORY
    assert [call.name for call in reply.tool_calls] == ['get_family_member']
    result = reply.tool_results[0]
    assert result.executed and result.data['members'][0]['name'] == 'Bina'
    # The model was given the database result, not left to guess.
    tool_messages = [m for m in provider.calls[-1]['messages'] if m.role == 'tool']
    assert any('Bina' in message.content for message in tool_messages)


def test_unknown_family_member_returns_no_data_to_invent_from(app):
    use_mock_llm(app, script=tool_then_answer(
        'get_family_member', 'I do not have that written down.', relation='nephew'))
    reply = app.conversation.handle(user_id='demo-user', message='What is my nephew called?',
                                    language='eng')
    result = reply.tool_results[0]
    assert result.data['found'] is False and result.data['members'] == []
    assert 'do not have' in reply.response_text.lower()


def test_meal_history_is_retrieved_for_yesterday(app):
    use_mock_llm(app, script=tool_then_answer(
        'get_meal_history', 'Yesterday you had poha, rice and dal, and roti and sabzi.',
        when='yesterday'))
    reply = app.conversation.handle(user_id='demo-user', message='What did I eat yesterday?',
                                    language='eng')
    meals = reply.tool_results[0].data
    assert meals['date'] == (date.today() - timedelta(days=1)).isoformat()
    assert {meal['description'] for meal in meals['meals']} == {
        'Poha', 'Rice and dal', 'Roti and sabzi'}


# --------------------------------------------------------------------------- #
# TEST 7 — weather must invoke the tool
# --------------------------------------------------------------------------- #
def test_weather_calls_the_weather_tool(app, weather_provider):
    use_mock_llm(app, script=tool_then_answer(
        'get_weather', 'It is about 29 degrees and partly cloudy today.'))
    reply = app.conversation.handle(user_id='demo-user', message='What is the weather today?',
                                    language='eng')
    assert weather_provider.calls == 1
    data = reply.tool_results[0].data
    assert data['available'] and data['temperature_c'] == 29.4


def test_weather_failure_is_reported_not_invented(app):
    from smriti_voice.exceptions import WeatherError

    class Broken:
        name = 'broken'

        def get_weather(self, **kwargs):
            raise WeatherError('network down')

    app.conversation.weather = Broken()
    use_mock_llm(app, script=tool_then_answer(
        'get_weather', 'I cannot check the weather right now.'))
    reply = app.conversation.handle(user_id='demo-user', message='What is the weather today?',
                                    language='eng')
    data = reply.tool_results[0].data
    assert data['available'] is False and data['reason'] == 'WEATHER_UNAVAILABLE'


# --------------------------------------------------------------------------- #
# TEST 8 — multi-turn context
# --------------------------------------------------------------------------- #
def test_history_is_passed_to_the_model_for_pronoun_resolution(app):
    provider = use_mock_llm(app, script=[
        *tool_then_answer('get_family_member', 'Bina is your daughter.', name='Bina'),
        *tool_then_answer('get_family_member', 'She lives in Shillong.', name='Bina'),
    ])
    first = app.conversation.handle(user_id='demo-user', message='Who is Bina?', language='eng')
    second = app.conversation.handle(user_id='demo-user', message='Where does she live?',
                                     session_id=first.session_id, language='eng')
    assert second.session_id == first.session_id
    texts = [message.content for message in provider.calls[-1]['messages']]
    assert any('Who is Bina?' in text for text in texts), 'history was not sent'


def test_history_is_bounded(app):
    use_mock_llm(app, reply='Yes.')
    session_id = None
    for index in range(12):
        reply = app.conversation.handle(user_id='demo-user', message=f'question {index}',
                                        session_id=session_id, language='eng')
        session_id = reply.session_id
    session = app.conversation.sessions.get_or_create(session_id, 'demo-user')
    assert len(session.turns) <= session.max_turns * 2


# --------------------------------------------------------------------------- #
# TEST 9 — confirmation
# --------------------------------------------------------------------------- #
def test_call_requires_a_yes_before_anything_happens(app):
    use_mock_llm(app, script=[tool_call_response('call_family_member', name='Bina')])
    first = app.conversation.handle(user_id='demo-user', message='How do I call Bina?',
                                    language='eng')
    assert first.requires_confirmation and first.kind is TurnKind.CONFIRMATION
    assert first.action == 'NO_ACTION' and not first.action_accepted
    assert 'yes' in first.response_text.lower()

    second = app.conversation.handle(user_id='demo-user', message='Yes.',
                                     session_id=first.session_id, language='eng')
    assert second.kind is TurnKind.CONFIRMATION
    assert second.action == 'CALL_PRIMARY_CONTACT' and second.action_accepted
    assert 'Bina' in second.response_text


def test_no_cancels_the_pending_action(app):
    use_mock_llm(app, script=[tool_call_response('call_family_member', name='Bina')])
    first = app.conversation.handle(user_id='demo-user', message='How do I call Bina?',
                                    language='eng')
    second = app.conversation.handle(user_id='demo-user', message='No',
                                     session_id=first.session_id, language='eng')
    assert second.action == 'NO_ACTION' and not second.action_accepted


def test_an_ambiguous_reply_never_counts_as_confirmation(app):
    use_mock_llm(app, script=[tool_call_response('call_family_member', name='Bina')])
    first = app.conversation.handle(user_id='demo-user', message='How do I call Bina?',
                                    language='eng')
    second = app.conversation.handle(user_id='demo-user', message='I am not sure, maybe later',
                                     session_id=first.session_id, language='eng')
    assert second.action == 'NO_ACTION'
    assert second.requires_confirmation, 'the confirmation should still be pending'


def test_confirmation_in_hindi(app):
    use_mock_llm(app, script=[tool_call_response('call_family_member', name='Bina')])
    first = app.conversation.handle(user_id='demo-user', message='Main Bina ko phone karna chahta hoon',
                                    language='hin')
    second = app.conversation.handle(user_id='demo-user', message='हाँ',
                                     session_id=first.session_id, language='hin')
    assert second.action == 'CALL_PRIMARY_CONTACT' and second.action_accepted


# --------------------------------------------------------------------------- #
# TEST 10 — phone help
# --------------------------------------------------------------------------- #
def test_phone_help_returns_one_step_at_a_time(app):
    use_mock_llm(app, script=tool_then_answer(
        'get_phone_help', 'Of course. First, look for the green circle with a white telephone.',
        topic='open whatsapp'))
    reply = app.conversation.handle(user_id='demo-user', message='How do I open WhatsApp?',
                                    language='eng')
    data = reply.tool_results[0].data
    assert data['found'] and len(data['steps']) == 3
    assert 'ONE step' in data['style']


# --------------------------------------------------------------------------- #
# TEST 11 — repetition must stay patient
# --------------------------------------------------------------------------- #
def test_repeating_the_same_question_is_answered_the_same_way(app):
    use_mock_llm(app, handler=lambda messages, tools: LLMResponse(
        text='Your daughter is called Bina.'))
    session_id = None
    answers = []
    for _ in range(4):
        reply = app.conversation.handle(user_id='demo-user',
                                        message="What is my daughter's name?",
                                        session_id=session_id, language='eng')
        session_id = reply.session_id
        answers.append(reply.response_text)
    assert len(set(answers)) == 1, 'the assistant changed its answer on repetition'


def test_system_prompt_forbids_shaming_the_user(app):
    provider = use_mock_llm(app, reply='ok')
    app.conversation.handle(user_id='demo-user', message='Please tell me a story about rain',
                            language='eng')
    system = provider.calls[-1]['system'].lower()
    assert 'never say "you already asked"' in system
    assert 'never scold' in system


# --------------------------------------------------------------------------- #
# TEST 13 — language switching
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('language,expected_name', [
    ('eng', 'English'), ('hin', 'Hindi'), ('asm', 'Assamese'), ('ben', 'Bengali'),
])
def test_system_prompt_names_the_current_language(app, language, expected_name):
    provider = use_mock_llm(app, reply='ok')
    app.conversation.handle(user_id='demo-user', message='hello', language=language)
    assert f'Answer in {expected_name}' in provider.calls[-1]['system']


def test_language_can_change_between_turns_in_one_session(app):
    provider = use_mock_llm(app, reply='ok')
    session_id = None
    for language in ('eng', 'hin', 'asm'):
        reply = app.conversation.handle(user_id='demo-user', message='hello',
                                        session_id=session_id, language=language)
        session_id = reply.session_id
        assert reply.language == language
    assert 'Assamese' in provider.calls[-1]['system']


# --------------------------------------------------------------------------- #
# Command path (v4.1) still short-circuits the model
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize('utterance,action', [
    ('open play', 'OPEN_PLAY'), ('show my family', 'OPEN_MY_PEOPLE'),
    ("what's today", 'OPEN_TODAY'), ('open medicine', 'OPEN_MEDICINE'),
    ('help', 'HELP'), ('stop', 'STOP'),
])
def test_commands_bypass_the_model_entirely(app, utterance, action):
    provider = use_mock_llm(app, reply='the model should not be called')
    reply = app.conversation.handle(user_id='demo-user', message=utterance, language='eng')
    assert reply.kind is TurnKind.COMMAND
    assert reply.action == action and reply.action_accepted
    assert provider.calls == [], 'a deterministic command reached the model'
