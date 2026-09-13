"""Every deterministic (non-LLM) response's `language` field must reflect
the language its text is ACTUALLY in, never the originally-requested or
detected language when no translated template exists for it.

Real, confirmed defect (reported in a Backend-team integration review and
reproduced directly against this repository's own source): requesting the
welcome greeting in Meiteilon ('mni') returned plain English text while
still reporting `language: "mni"` -- exactly the "silent substitution"
failure mode this codebase already treats as unacceptable for TTS/audio
(see INTEGRATION_CONTRACT.md), just not previously enforced for text.
Every deterministic template in this codebase (welcome, safety refusals,
confirmation prompts, action replies, the deterministic fallback) only
ever covers eng/hin/asm/ben and falls back to English text for anything
else -- these tests prove the reported `language` now falls back with it.
"""
from __future__ import annotations

from smriti_voice.schemas import TurnKind

from test_conversation_scenarios import tool_call_response, use_mock_llm

# A language that is real, ASR-online, and has no deterministic template
# translation anywhere in this codebase -- the exact language the original
# defect was reported against.
UNTEMPLATED_LANGUAGE = 'mni'


def test_welcome_reports_english_when_no_translated_greeting_exists(app):
    outcome = app.conversation.welcome(user_id='demo-user', language=UNTEMPLATED_LANGUAGE)
    assert outcome.language == 'eng'
    assert 'Hello' in outcome.text  # the actual English template, not a mni one


def test_welcome_still_reports_the_real_language_when_a_translation_exists(app):
    """The fix must not downgrade languages that DO have a template."""
    outcome = app.conversation.welcome(user_id='demo-user', language='hin')
    assert outcome.language == 'hin'
    assert 'नमस्ते' in outcome.text


def test_deterministic_safety_refusal_reports_english_for_an_untemplated_language(app):
    resp = app.conversation.handle(user_id='demo-user', message='change my medicine dose',
                                   language=UNTEMPLATED_LANGUAGE)
    assert resp.kind is TurnKind.REFUSAL
    assert resp.language == 'eng'


def test_command_action_reply_reports_english_for_an_untemplated_language(app):
    resp = app.conversation.handle(user_id='demo-user', message='open play',
                                   language=UNTEMPLATED_LANGUAGE)
    assert resp.kind is TurnKind.COMMAND
    assert resp.language == 'eng'


def test_confirmation_prompt_and_resolution_report_english_for_an_untemplated_language(app):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Take a walk',
                                                  remind_at='18:00')])
    proposed = app.conversation.handle(user_id='demo-user', message='remind me to take a walk',
                                       language=UNTEMPLATED_LANGUAGE)
    assert proposed.requires_confirmation is True
    assert proposed.language == 'eng'

    confirmed = app.conversation.handle(user_id='demo-user', message='yes',
                                        session_id=proposed.session_id,
                                        language=UNTEMPLATED_LANGUAGE)
    assert confirmed.language == 'eng'


def test_declining_a_confirmation_reports_english_for_an_untemplated_language(app):
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Take a walk',
                                                  remind_at='18:00')])
    proposed = app.conversation.handle(user_id='demo-user', message='remind me to take a walk',
                                       language=UNTEMPLATED_LANGUAGE)
    declined = app.conversation.handle(user_id='demo-user', message='no',
                                       session_id=proposed.session_id,
                                       language=UNTEMPLATED_LANGUAGE)
    assert declined.language == 'eng'


def test_session_language_itself_is_not_downgraded_by_one_templated_fallback(app):
    """The requested/effective language for the CONVERSATION must stay
    Meiteilon even though one deterministic response had to fall back to
    English text -- only that one response's own `language` field is
    downgraded, not the patient's actual language going forward."""
    app.conversation.welcome(user_id='demo-user', language=UNTEMPLATED_LANGUAGE)
    session = app.conversation.sessions.get_or_create(None, 'demo-user', 'eng')
    # A fresh get_or_create(None, ...) always mints a new session, so
    # instead check via a real turn that session.language tracks mni:
    reply = app.conversation.handle(user_id='demo-user', message='open play',
                                    language=UNTEMPLATED_LANGUAGE)
    stored = app.conversation.sessions.get_or_create(reply.session_id, 'demo-user', 'eng')
    assert stored.language == UNTEMPLATED_LANGUAGE


def test_voice_asr_error_text_reports_english_for_an_untemplated_language(app, tmp_path):
    import io
    import wave
    from smriti_voice.pipeline import VoicePipeline

    buf = io.BytesIO()
    with wave.open(buf, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x00\x00' * 8000)
    wav_path = tmp_path / 'silent.wav'
    wav_path.write_bytes(buf.getvalue())

    def _raise(*a, **k):
        raise RuntimeError('simulated ASR failure')
    app.asr.transcribe = _raise

    reply = VoicePipeline(app).process(wav_path, user_id='demo-user',
                                       language=UNTEMPLATED_LANGUAGE, speak=False)
    assert reply.audio_unavailable_reason == 'ASR_UNAVAILABLE'
    assert reply.language == 'eng'
    assert 'I could not hear' in reply.response_text
