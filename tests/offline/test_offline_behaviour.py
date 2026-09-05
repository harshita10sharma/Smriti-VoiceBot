"""Offline and degraded operation.

With no LLM reachable, the saved-information questions the specification lists
must still be answerable, and anything needing the internet must be refused
honestly rather than guessed.
"""
from __future__ import annotations

import pytest

from smriti_voice.exceptions import LLMError, ProviderNotConfigured
from smriti_voice.schemas import ExecutionMode, TurnKind


@pytest.fixture
def offline_app(app):
    """No LLM at all: every provider raises."""
    class DeadLLM:
        name = 'dead'
        online = True
        supports_tools = True

        def generate(self, *args, **kwargs):
            raise LLMError('no provider reachable')

    app.llm._cache['mock'] = DeadLLM()
    return app


@pytest.mark.parametrize('utterance,expected_fragment', [
    ("what is my daughter's name", 'Bina'),
    ('what did I eat yesterday', 'Poha'),
    ('what medicine do I take in the morning', 'Amlodipine'),
    ('what are my reminders', None),
    ('my games', 'Memory Match'),
    ('who is visiting me', 'Rakesh'),
])
def test_saved_questions_still_answered_without_any_model(offline_app, utterance,
                                                          expected_fragment):
    reply = offline_app.conversation.handle(user_id='demo-user', message=utterance,
                                            language='eng')
    assert reply.kind is TurnKind.FALLBACK
    assert reply.metadata.fallback_used
    if expected_fragment:
        assert expected_fragment in reply.response_text


def test_internet_questions_are_refused_honestly_offline(offline_app):
    reply = offline_app.conversation.handle(user_id='demo-user',
                                            message='why is the sky blue', language='eng')
    assert reply.kind is TurnKind.FALLBACK
    assert reply.metadata.error_code == 'NO_GENERAL_AI_AVAILABLE'
    assert 'cannot look that up' in reply.response_text.lower()


def test_commands_still_work_offline(offline_app):
    reply = offline_app.conversation.handle(user_id='demo-user', message='open play',
                                            language='eng')
    assert reply.kind is TurnKind.COMMAND and reply.action == 'OPEN_PLAY'


def test_safety_still_holds_offline(offline_app):
    reply = offline_app.conversation.handle(user_id='demo-user', message='delete my medicine',
                                            language='eng')
    assert reply.kind is TurnKind.REFUSAL


def test_offline_fallback_speaks_the_users_language(offline_app):
    reply = offline_app.conversation.handle(
        user_id='demo-user', message='what medicine do I take in the morning', language='hin')
    assert reply.language == 'hin'
    assert any(ord(character) > 0x900 for character in reply.response_text)


def test_language_without_reviewed_templates_says_so(offline_app):
    """Manipuri has no reviewed offline wording, so we must not answer in it."""
    reply = offline_app.conversation.handle(
        user_id='demo-user', message='what medicine do I take in the morning', language='mni')
    assert reply.kind is TurnKind.FALLBACK
    assert reply.response_text
    # It must not fabricate Manipuri: the honest English notice is used instead.
    assert 'not connected' in reply.response_text.lower()


def test_local_llm_is_not_bundled_and_says_so(monkeypatch):
    from smriti_voice.llm.local import LocalLLMProvider
    monkeypatch.delenv('SMRITI_LOCAL_LLM_URL', raising=False)
    with pytest.raises(ProviderNotConfigured) as excinfo:
        LocalLLMProvider()
    assert 'SMRITI_LOCAL_LLM_URL' in str(excinfo.value)


def test_local_tts_is_not_bundled_and_supports_nothing_by_default(monkeypatch):
    from smriti_voice.tts.local import LocalTTSProvider
    monkeypatch.delenv('SMRITI_LOCAL_TTS_URL', raising=False)
    monkeypatch.delenv('SMRITI_LOCAL_TTS_LANGUAGES', raising=False)
    provider = LocalTTSProvider()
    assert provider.supports('hin') is False


def test_forced_offline_never_selects_a_cloud_provider(monkeypatch):
    from smriti_voice.config import AppConfig
    from smriti_voice.llm.router import LLMRouter
    monkeypatch.setenv('SMRITI_FORCE_OFFLINE', '1')
    monkeypatch.setenv('SMRITI_LLM_PROVIDER', 'auto')
    monkeypatch.setenv('GEMINI_API_KEY', 'set-but-must-not-be-used')
    router = LLMRouter(AppConfig.load())
    assert router.candidates(needs_tools=True) == ['local']


def test_execution_mode_degrades_rather_than_erroring(app):
    mode = app.offline.mode(llm_available=False, local_llm_available=False)
    assert mode is ExecutionMode.DEGRADED
