"""Automatic per-turn language detection and propagation, for both text
and voice, verified against the actual current implementation rather than
assumed.

Audit finding (see CHANGELOG.md): text detection was already wired up
correctly at the route boundary (api/routes/conversation.py already calls
app.detector.detect(message) whenever no explicit language is supplied,
before this file existed) -- the existing LanguageDetector
(language/detector.py) already provides confidence-scored detection and
is reused as-is here, not replaced. The one genuine gap found and fixed:
a zero-signal detection (digits/punctuation-only text, method='default',
confidence=0.0) was being treated as a real detection and forced 'eng'
over an ongoing session's actual language -- fixed in both
api/routes/conversation.py and pipeline.py to fall through to the
session's existing language in that specific case only.
"""
from __future__ import annotations

import io
import tempfile
import wave
from pathlib import Path

from smriti_voice.schemas import ASRResult

from test_conversation_scenarios import use_mock_llm


def _silent_wav_path(tmp_path: Path, seconds: float = 0.5) -> Path:
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b'\x00\x00' * int(16000 * seconds))
    path = tmp_path / 'silent.wav'
    path.write_bytes(buf.getvalue())
    return path


# --------------------------------------------------------------------------- #
# TEST 1-3: text auto-detection
# --------------------------------------------------------------------------- #
def test_english_text_is_detected_and_answered_in_english(client, auth_headers, app):
    use_mock_llm(app, reply='I am here to help.')
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'message': 'Hello, how are you?'})
    assert resp.status_code == 200
    assert resp.json()['language'] == 'eng'


def test_hindi_text_is_detected_and_answered_in_hindi(client, auth_headers, app):
    use_mock_llm(app, reply='Main theek hoon.')
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'message': 'नमस्ते, आप कैसे हैं?'})
    assert resp.status_code == 200
    assert resp.json()['language'] == 'hin'


def test_assamese_text_is_detected_and_answered_in_assamese(client, auth_headers, app):
    """Assamese/Bengali share a script; the existing detector separates
    them only via Assamese-only letters (see language/detector.py's
    ASSAMESE_MARKERS) -- this sample contains one ('ৰ') so it detects
    correctly. A sample without any such letter detecting as 'ben' instead
    is a pre-existing, documented detector limitation (see
    STATIC_LIMITATIONS in language/capabilities.py), not a regression this
    file introduces or is required to fix."""
    use_mock_llm(app, reply='Moi bhal asu.')
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user',
                            'message': 'আপোনাৰ সৈতে কথা পাতি ভাল লাগিল।'})
    assert resp.status_code == 200
    assert resp.json()['language'] == 'asm'


# --------------------------------------------------------------------------- #
# TEST 4-6: voice auto-detection (ASR-driven)
# --------------------------------------------------------------------------- #
def test_english_voice_is_detected_and_answered_in_english(app, tmp_path):
    from smriti_voice.pipeline import VoicePipeline
    app.asr.transcribe = lambda *a, **k: ASRResult(
        transcript='Hello, how are you today?', language='en', language_confidence=0.95,
        provider='stub')
    use_mock_llm(app, reply='I am here to help.')
    reply = VoicePipeline(app).process(_silent_wav_path(tmp_path), user_id='demo-user',
                                       speak=False)
    assert reply.language == 'eng'


def test_hindi_voice_is_detected_and_answered_in_hindi(app, tmp_path):
    from smriti_voice.pipeline import VoicePipeline
    app.asr.transcribe = lambda *a, **k: ASRResult(
        transcript='नमस्ते, आप कैसे हैं?', language='hi', language_confidence=0.95,
        provider='stub')
    use_mock_llm(app, reply='Main theek hoon.')
    reply = VoicePipeline(app).process(_silent_wav_path(tmp_path), user_id='demo-user',
                                       speak=False)
    assert reply.language == 'hin'


def test_another_supported_voice_language_propagates_to_response_and_tts_job(app, tmp_path):
    """Assamese: ASR-online but no Sarvam TTS coverage -- exercises a
    voice turn for a language that isn't English/Hindi, propagated
    through to the queued TTS job's language field."""
    from smriti_voice.pipeline import VoicePipeline
    app.asr.transcribe = lambda *a, **k: ASRResult(
        transcript='আপোনাৰ সৈতে কথা পাতি ভাল লাগিল।', language='as',
        language_confidence=0.95, provider='stub')
    use_mock_llm(app, reply='Moi bhal asu.')
    reply = VoicePipeline(app).process(_silent_wav_path(tmp_path), user_id='demo-user',
                                       speak=True)
    assert reply.language == 'asm'
    job = app.voice_jobs.get(reply.job_id)
    assert job.language == 'asm'  # the queued TTS job got the same effective language


# --------------------------------------------------------------------------- #
# TEST 7-8: no explicit language must use real detection, not default
# --------------------------------------------------------------------------- #
def test_text_without_explicit_language_never_silently_uses_default_over_a_confident_detection(
        client, auth_headers, app):
    use_mock_llm(app, reply='Main theek hoon.')
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'message': 'मुझे मदद चाहिए'})
    assert resp.json()['language'] == 'hin'  # not 'eng', the configured default


def test_voice_without_explicit_language_uses_asr_detection(app, tmp_path):
    from smriti_voice.pipeline import VoicePipeline
    app.asr.transcribe = lambda *a, **k: ASRResult(
        transcript='मुझे मदद चाहिए', language='hi', language_confidence=0.9, provider='stub')
    use_mock_llm(app, reply='Main theek hoon.')
    reply = VoicePipeline(app).process(_silent_wav_path(tmp_path), user_id='demo-user',
                                       language=None, speak=False)
    assert reply.language == 'hin'


# --------------------------------------------------------------------------- #
# TEST 9: language changes naturally within one session
# --------------------------------------------------------------------------- #
def test_language_changes_within_the_same_session_are_tracked_per_turn(client, auth_headers,
                                                                        app):
    use_mock_llm(app, reply='OK.')
    r1 = client.post('/v1/conversation', headers=auth_headers,
                     json={'user_id': 'demo-user', 'message': 'Hello there'})
    sid = r1.json()['session_id']
    assert r1.json()['language'] == 'eng'

    r2 = client.post('/v1/conversation', headers=auth_headers,
                     json={'user_id': 'demo-user', 'session_id': sid,
                          'message': 'नमस्ते, कैसे हैं आप'})
    assert r2.json()['language'] == 'hin'

    r3 = client.post('/v1/conversation', headers=auth_headers,
                     json={'user_id': 'demo-user', 'session_id': sid,
                          'message': 'Thank you, goodbye'})
    assert r3.json()['language'] == 'eng'


def test_zero_signal_input_preserves_the_sessions_current_language_instead_of_forcing_default(
        client, auth_headers, app):
    """The genuine gap found and fixed this pass: digits/punctuation carry
    no language signal at all (detector method='default', confidence 0.0)
    -- that must fall back to the session's real language, never silently
    force the configured default over an active non-English conversation."""
    use_mock_llm(app, reply='Main theek hoon.')
    r1 = client.post('/v1/conversation', headers=auth_headers,
                     json={'user_id': 'demo-user', 'message': 'नमस्ते, आप कैसे हैं?'})
    sid = r1.json()['session_id']
    assert r1.json()['language'] == 'hin'

    r2 = client.post('/v1/conversation', headers=auth_headers,
                     json={'user_id': 'demo-user', 'session_id': sid, 'message': '123'})
    assert r2.json()['language'] == 'hin'  # not forced to 'eng'


# --------------------------------------------------------------------------- #
# TEST 10: mixed-language input -- document actual behavior, don't invent
# a new classifier
# --------------------------------------------------------------------------- #
def test_mixed_language_input_uses_the_existing_detectors_dominant_script_result(
        client, auth_headers, app):
    """No new multilingual classifier was introduced (explicitly out of
    scope). The existing detector picks the script with the most letters
    by raw count -- for 'मुझे सुबह breakfast के बाद medicine लेनी है',
    the English loanwords ('breakfast', 'medicine') are long enough that
    Latin-script letters actually outnumber Devanagari ones, so the
    existing, unmodified detector reports 'eng'. This pins that real,
    documented behavior rather than asserting an invented "correct"
    answer the detector was never designed to produce."""
    use_mock_llm(app, reply='OK.')
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user',
                            'message': 'मुझे सुबह breakfast के बाद medicine लेनी है'})
    assert resp.json()['language'] == 'eng'

    # And a message that is overwhelmingly Hindi script with only a
    # trailing English tag word must NOT flip to English merely because
    # one English word is present -- the dominant script (Hindi) wins.
    resp2 = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user',
                            'message': 'मुझे आज दोपहर को अपनी दवाई लेनी है और फिर आराम करना है ok'})
    assert resp2.json()['language'] == 'hin'


# --------------------------------------------------------------------------- #
# TEST 11: no silent substitution when TTS can't speak the detected language
# --------------------------------------------------------------------------- #
def test_tts_router_refuses_an_unsupported_language_rather_than_substituting(monkeypatch):
    """Direct check on the actual mechanism: when no configured TTS
    provider covers the detected language, TTSRouter must report
    unavailable with an accurate reason, never silently pick a different
    provider/language's voice. Uses the same isolated-router pattern as
    tests/multilingual/test_language_routing.py -- the shared `app`
    fixture always forces SMRITI_TTS_PROVIDER=mock (which "succeeds" for
    every language by design, so it cannot exercise this gap at all)."""
    from smriti_voice.config import AppConfig
    from smriti_voice.language.registry import LanguageService
    from smriti_voice.tts.router import TTSRouter
    monkeypatch.setenv('SMRITI_TTS_PROVIDER', 'auto')
    monkeypatch.setenv('SARVAM_API_KEY', 'not-used-no-call-is-made')
    monkeypatch.setenv('SMRITI_INDIC_PARLER_ENABLED', '0')
    router = TTSRouter(AppConfig.load(), LanguageService())
    result = router.synthesize('আপোনাৰ সৈতে কথা পাতি ভাল লাগিল।', 'asm')
    assert result.available is False
    assert result.unavailable_reason == 'NO_TTS_PROVIDER_SUPPORTS_LANGUAGE'
    assert result.provider == 'none'  # never a real provider's name for audio that doesn't exist


def test_response_text_and_effective_language_are_unaffected_by_tts_availability(app, tmp_path):
    """The detected language and the LLM's response text must reflect the
    actual detected language regardless of whether TTS can speak it --
    text correctness and audio availability are independent guarantees."""
    from smriti_voice.pipeline import VoicePipeline
    app.asr.transcribe = lambda *a, **k: ASRResult(
        transcript='আপোনাৰ সৈতে কথা পাতি ভাল লাগিল।', language='as',
        language_confidence=0.95, provider='stub')
    use_mock_llm(app, reply='Moi bhal asu, apunar logot kotha patisu.')
    reply = VoicePipeline(app).process(_silent_wav_path(tmp_path), user_id='demo-user',
                                       speak=True)
    assert reply.language == 'asm'
    assert reply.response_text == 'Moi bhal asu, apunar logot kotha patisu.'


# --------------------------------------------------------------------------- #
# TEST 12: deterministic responses use the effective language
# --------------------------------------------------------------------------- #
def test_deterministic_safety_refusal_uses_the_effective_detected_language(client, auth_headers,
                                                                            app):
    """Real finding, out of this fix's scope but worth recording: the
    deterministic safety screen's keyword lists (config/safety.json) are
    English-only ('medicine', 'dose', etc.) -- a Hindi-script phrase
    asking to change a dose ('meri dawai ki khurak badal do') does not
    match them and is not deterministically refused at all, it falls
    through to the LLM. That is a separate, broader safety-coverage gap,
    not a language-detection/propagation bug, and translating every
    safety keyword list is out of scope for this pass. What this test
    verifies instead is the actual mechanism this fix is responsible
    for: once a refusal IS triggered, its text renders in the current
    effective language, using an English trigger phrase (guaranteed to
    match) with the effective language explicitly set to Hindi."""
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'language': 'hin',
                            'message': 'change my medicine dose'})
    assert resp.status_code == 200
    body = resp.json()
    assert body['language'] == 'hin'
    assert body['kind'] == 'REFUSAL'
    # The refusal text itself is in Hindi script, not the English fallback.
    assert any('ऀ' <= ch <= 'ॿ' for ch in body['response_text'])


def test_deterministic_confirmation_prompt_uses_the_effective_detected_language(
        client, auth_headers, app):
    from test_conversation_scenarios import tool_call_response
    use_mock_llm(app, script=[tool_call_response('create_reminder', text='Take a walk',
                                                  remind_at='18:00')])
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user',
                            'message': 'मुझे टहलने के लिए याद दिलाओ'})
    body = resp.json()
    assert body['requires_confirmation'] is True
    assert any('ऀ' <= ch <= 'ॿ' for ch in body['response_text'])


# --------------------------------------------------------------------------- #
# TEST 13: cross-patient/session behavior is unaffected
# --------------------------------------------------------------------------- #
def test_language_detection_does_not_affect_cross_patient_isolation(app, client, auth_headers):
    from smriti_voice.memory.models import User
    app.memory.repo.upsert_user(User(user_id='other-lang-patient', display_name='Other'))
    use_mock_llm(app, reply='Hello there.')
    owner = client.post('/v1/conversation', headers=auth_headers,
                        json={'user_id': 'demo-user', 'message': 'नमस्ते'})
    sid = owner.json()['session_id']
    hijack = client.post('/v1/conversation', headers=auth_headers,
                         json={'user_id': 'other-lang-patient', 'session_id': sid,
                              'message': 'नमस्ते'})
    assert hijack.status_code == 403


# --------------------------------------------------------------------------- #
# TEST 14: existing normalization aliases still work end to end
# --------------------------------------------------------------------------- #
def test_explicit_client_alias_still_normalizes_correctly_alongside_auto_detection(
        client, auth_headers, app):
    use_mock_llm(app, reply='Main theek hoon.')
    # Explicit alias always wins over detection, and still normalizes.
    resp = client.post('/v1/conversation', headers=auth_headers,
                       json={'user_id': 'demo-user', 'message': 'Hello', 'language': 'hi'})
    assert resp.json()['language'] == 'hin'
