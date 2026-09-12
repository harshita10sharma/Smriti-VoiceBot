"""The forensic audit found a real gap: /v1/command's legacy VoiceEngine
path recognized 'call Bina'/CALL_PRIMARY_CONTACT and reported
accepted=True with zero confirmation step, unlike the conversational path
(POST /v1/conversation), which always stops for an explicit yes/no before
placing a call. /v1/command has no session and no multi-turn state at all,
so a real confirmation flow cannot be built there -- the safe fix is that
a call action is recognized (the `action` field still reports it) but
never authorized (`accepted` stays False), the same signal every other
unaccepted action already uses.

The router (IntentRouter) and the allow-list gate (SafeActionGate) are
unaffected and unchanged -- see tests/safety/test_v41_regression.py, which
still asserts 'call Bina' is accepted *by the router* and passes the gate.
This fix is one layer up, in VoiceEngine.process itself.
"""
from __future__ import annotations

from pathlib import Path

from smriti_voice.config import Settings
from smriti_voice.engine import VoiceEngine


class _StubProvider:
    name = 'stub'

    def __init__(self, transcript: str):
        self._transcript = transcript

    def transcribe(self, wav, language=None):
        return self._transcript, language, 1.0


def _engine_with_stub(transcript: str) -> VoiceEngine:
    engine = VoiceEngine(Settings.load())
    engine._local_provider = lambda language: _StubProvider(transcript)
    return engine


def test_call_bina_is_recognized_but_never_accepted(tmp_path):
    engine = _engine_with_stub('call Bina')
    result = engine.process(tmp_path / 'input.wav', 'eng')
    assert result.action == 'CALL_BINA'
    assert result.accepted is False
    assert result.reason == 'call_requires_confirmation_use_conversation_endpoint'


def test_non_call_commands_are_unaffected(tmp_path):
    engine = _engine_with_stub('open medicine')
    result = engine.process(tmp_path / 'input.wav', 'eng')
    assert result.action == 'OPEN_MEDICINE'
    assert result.accepted is True


def test_help_and_stop_are_unaffected(tmp_path):
    for transcript, expected_action in (('help', 'HELP'), ('stop', 'STOP')):
        engine = _engine_with_stub(transcript)
        result = engine.process(tmp_path / 'input.wav', 'eng')
        assert result.action == expected_action
        assert result.accepted is True


def test_unsafe_utterances_are_still_rejected_before_reaching_the_call_check(tmp_path):
    engine = _engine_with_stub('delete my medicine')
    result = engine.process(tmp_path / 'input.wav', 'eng')
    assert result.action == 'NO_ACTION'
    assert result.accepted is False
