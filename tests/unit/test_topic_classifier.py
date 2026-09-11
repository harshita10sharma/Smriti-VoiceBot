"""conversation/classifier.py: the deterministic PERSONAL/GENERAL hint.

Pure function, no I/O -- these are plain unit tests. The end-to-end proof
that this actually changes what the LLM is told lives in
tests/integration/test_conversation_scenarios.py's general-knowledge
regression tests, which inspect the real system prompt built by
ConversationManager._converse.
"""
from __future__ import annotations

from smriti_voice.conversation.classifier import classify_topic


def test_general_knowledge_questions_are_general():
    assert classify_topic('Tell me an Assamese recipe.') == 'general'
    assert classify_topic('What is Assamese cuisine?') == 'general'
    assert classify_topic('What is Metformin?') == 'general'
    assert classify_topic('How far is the moon from the earth?') == 'general'
    assert classify_topic('What is the capital of India?') == 'general'


def test_personal_questions_are_personal():
    assert classify_topic('What is my daughter\'s name?') == 'personal'
    assert classify_topic('What medicine do I take tonight?') == 'personal'
    assert classify_topic('Who is visiting me today?') == 'personal'
    assert classify_topic('Call my daughter.') == 'personal'  # ACTION already
    # handled earlier in _route; classify_topic only sees this text if the
    # turn ever reaches _converse, which is still fine for it to call
    # 'personal' since it plainly is about the user's own family.


def test_generic_conversational_words_never_trigger_personal_alone():
    """Explicit requirement: single generic words must never, by
    themselves, make an ordinary question look personal."""
    for message in (
        'What is your name?',
        'What time is it?',
        'When should I water plants?',
        'Tell me a story.',
        'Can you help me understand this?',
        'What is today\'s date in history?',
        'Please give me an example.',
    ):
        assert classify_topic(message) == 'general', message


def test_word_boundary_matching_does_not_false_positive_on_substrings():
    """'son' must not match inside an unrelated word like 'season' or
    'reason' -- a real risk of naive substring matching."""
    assert classify_topic('What is the best season to visit Assam?') == 'general'
    assert classify_topic('Give me a reason to smile today.') == 'general'


def test_medicine_general_knowledge_vs_personal_medicine_query():
    assert classify_topic('What is Metformin used for?') == 'general'
    assert classify_topic('When is my medicine?') == 'personal'
    assert classify_topic('What medicine do I take tonight?') == 'personal'
