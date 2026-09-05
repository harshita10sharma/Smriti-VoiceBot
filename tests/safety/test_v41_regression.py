"""Regression pins for the safety-critical behaviour that shipped in VoiceBot v4.1.

These cases were executed against the unmodified v4.1 router before any migration
work started and produced exactly these results.  If a future change to the
conversational layer, the safety package or the language packs alters any line
here, the migration has broken existing production behaviour.
"""
from pathlib import Path

import pytest

from smriti_voice.actions import Action, SafeActionGate
from smriti_voice.intents import IntentRouter

ROOT = Path(__file__).resolve().parents[2]
PACK = ROOT / 'language_packs' / 'commands_eng.json'

# (utterance, expected intent) — verified accepted by v4.1.
SAFE_CASES = [
    ('open play', 'OPEN_PLAY'),
    ('play', 'OPEN_PLAY'),
    ('show my family', 'OPEN_MY_PEOPLE'),
    ('show my people', 'OPEN_MY_PEOPLE'),
    ("what's today", 'OPEN_TODAY'),
    ('help', 'HELP'),
    ('stop', 'STOP'),
    ('medicine', 'OPEN_MEDICINE'),
    ('open medicine', 'OPEN_MEDICINE'),
    ('call Bina', 'CALL_BINA'),
]

# Verified rejected by v4.1 with reason `unsafe_conflicting_request`.
UNSAFE_CASES = [
    'delete my medicine',
    'change my dosage',
    'remove my medicine',
    'transfer money',
    'call 9876543210',
    'delete my family',
    'change my game',
]

# Additional unsafe phrases already covered by the v4.1 test suite.
LEGACY_UNSAFE_CASES = [
    'change my medicine', 'delete medicine', 'cancel my medicine', 'stop my medicine',
    'call +91 98765 43210', 'remove medicine', 'change my family', 'delete family',
    'cancel family', 'stop my today', 'delete my people', 'change my prescription',
    'remove my prescription', 'cancel my routine', 'transfer account money',
]


@pytest.fixture(scope='module')
def router() -> IntentRouter:
    return IntentRouter(PACK)


@pytest.mark.parametrize('utterance,expected_intent', SAFE_CASES)
def test_safe_commands_still_accepted(router, utterance, expected_intent):
    match = router.classify(utterance)
    assert match.accepted, f'{utterance!r} must remain accepted (reason={match.reason})'
    assert match.intent == expected_intent
    assert match.reason == 'accepted'


@pytest.mark.parametrize('utterance,expected_intent', SAFE_CASES)
def test_safe_commands_survive_the_action_gate(router, utterance, expected_intent):
    match = router.classify(utterance)
    granted = SafeActionGate().authorize(match.action, match.intent)
    assert granted.action != Action.NO_ACTION


@pytest.mark.parametrize('utterance', UNSAFE_CASES + LEGACY_UNSAFE_CASES)
def test_unsafe_commands_fail_closed(router, utterance):
    match = router.classify(utterance)
    assert not match.accepted, f'{utterance!r} was accepted as {match.action}'
    assert match.action == 'NO_ACTION'
    assert match.reason == 'unsafe_conflicting_request'


@pytest.mark.parametrize('utterance', UNSAFE_CASES + LEGACY_UNSAFE_CASES)
def test_unsafe_commands_never_reach_an_executable_action(router, utterance):
    match = router.classify(utterance)
    granted = SafeActionGate().authorize(match.action, match.intent)
    assert granted.action == Action.NO_ACTION


def test_action_gate_rejects_anything_outside_the_allow_list():
    gate = SafeActionGate()
    for hostile in ['DROP_DATABASE', 'TRANSFER_MONEY', 'DELETE_MEDICINE', 'rm -rf /', '', 'OPEN_PLAY; DROP']:
        assert gate.authorize(hostile, 'UNKNOWN').action == Action.NO_ACTION


def test_language_pack_without_phrases_fails_closed():
    """An unvalidated pack must reject everything, never borrow another language."""
    router = IntentRouter(ROOT / 'language_packs' / 'commands_mni.json')
    match = router.classify('anything at all')
    assert not match.accepted and match.action == 'NO_ACTION'
