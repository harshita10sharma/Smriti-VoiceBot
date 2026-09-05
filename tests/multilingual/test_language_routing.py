"""Language following, and honest reporting when a language cannot be spoken."""
from __future__ import annotations

import pytest

from smriti_voice.language.registry import LanguageService
from smriti_voice.schemas import TurnKind
from smriti_voice.tts.mock import MockTTSProvider


LANGUAGES = ['eng', 'hin', 'asm', 'ben']


@pytest.mark.parametrize('language', LANGUAGES)
def test_refusal_and_command_replies_exist_in_each_reviewed_language(app, language):
    from smriti_voice.conversation.manager import ACTION_REPLIES
    from smriti_voice.safety.policy import REFUSALS
    for table in REFUSALS.values():
        assert language in table, f'missing refusal text for {language}'
    for table in ACTION_REPLIES.values():
        assert language in table, f'missing action reply for {language}'


@pytest.mark.parametrize('language', LANGUAGES)
def test_turn_responds_in_the_requested_language(app, language):
    reply = app.conversation.handle(user_id='demo-user', message='delete my medicine',
                                    language=language)
    assert reply.language == language
    assert reply.kind is TurnKind.REFUSAL


def test_tts_refuses_to_speak_a_language_it_does_not_support(app, monkeypatch):
    """Assamese must produce 'no voice' — never Bengali audio labelled Assamese."""
    from smriti_voice.config import AppConfig
    from smriti_voice.tts.router import TTSRouter
    monkeypatch.setenv('SMRITI_TTS_PROVIDER', 'auto')
    monkeypatch.setenv('SARVAM_API_KEY', 'not-used-no-call-is-made')
    router = TTSRouter(AppConfig.load(), LanguageService())
    result = router.synthesize('নমস্কাৰ', 'asm')
    assert result.available is False
    assert result.unavailable_reason == 'NO_TTS_PROVIDER_SUPPORTS_LANGUAGE'


def test_tts_language_tag_is_never_silently_substituted(app):
    languages = LanguageService()
    assert languages.tts_language_tag('hin') == 'hi-IN'
    assert languages.tts_language_tag('asm') is None
    assert languages.can_speak('asm') is False


def test_mock_tts_records_the_language_it_was_asked_for(app):
    provider = MockTTSProvider()
    provider.synthesize('hello', 'hin')
    assert provider.calls == [('hin', 'hello')]


def test_unsupported_language_is_rejected_by_the_api(client, auth_headers):
    response = client.post('/v1/conversation', headers=auth_headers,
                           json={'user_id': 'demo-user', 'message': 'hello',
                                 'language': 'klingon'})
    assert response.status_code == 400
