"""A deterministic PERSONAL-vs-GENERAL topic hint for the LLM system prompt.

This module never decides the final answer and never bypasses anything: by
the time it runs (inside ``ConversationManager._converse``), the injection
screen, the sensitive-request safety screen, any pending confirmation and
the v4.1 deterministic command router have already had first refusal
(``conversation/manager.py:_route``, unchanged). All this module does is
tell the model, in plain language, whether the question in front of it looks
like it is about the user's own saved data (so it must call a tool and never
guess) or looks like general knowledge (so it should just answer, rather
than deflecting to "I don't have that written down, ask your caregiver" --
a real, observed failure mode for questions like "tell me an Assamese
recipe" or "what is Metformin?", which are about the world, not about this
particular person).

Matching is deliberately narrow: multi-word phrases, or single words drawn
only from a short list of domain-specific nouns (family relations, and the
words "medicine"/"appointment"/"routine"/etc.), each checked on a real word
boundary. Ordinary conversational words such as "your", "time", "when",
"today", "help", "tell" or "give" are never, by themselves, enough to call a
question personal -- a question containing only those words falls through
to GENERAL, which is the safer default: the model still has the tools
available and can call one if it turns out to need to, but it is not primed
to see a caregiver-escalation as the shape of a correct answer to a general
question.
"""
from __future__ import annotations

import re
from typing import Literal

from ..normalize import normalize_text

Topic = Literal['personal', 'general']

# Multi-word phrases are safe to match as plain substrings: a phrase this
# specific to the user's own data cannot appear inside an unrelated
# general-knowledge question by coincidence.
_PERSONAL_PHRASES: tuple[str, ...] = (
    'my family', 'my daughter', 'my son', 'my wife', 'my husband',
    'my sister', 'my brother', 'my grandson', 'my granddaughter',
    'my people', 'my medicine', 'my medicines', 'my medication',
    'my dose', 'my dosage', 'my pills', 'my appointment', 'my appointments',
    'my routine', 'my schedule', 'my reminder', 'my reminders',
    'my visitor', 'my visitors', 'my game', 'my games',
    'what medicine do i take', 'when is my medicine', 'medicine time',
    'do i have an appointment', 'who is visiting', 'visiting me today',
    'what did i eat', 'ate yesterday', 'ate today', 'take tonight',
    'take today', 'take this morning',
)

# Single words matched on a real word boundary only, and drawn only from
# nouns specific enough to a personal-data domain that they are not part of
# everyday conversation. Deliberately excludes generic function words
# ("your", "time", "when", "today", "help", "tell", "give", ...), which say
# nothing about topic on their own.
_PERSONAL_WORDS: tuple[str, ...] = (
    'daughter', 'son', 'wife', 'husband', 'grandson', 'granddaughter',
    'sister', 'brother', 'caregiver', 'medication',
)

_WORD_PATTERNS = [re.compile(rf'\b{re.escape(word)}\b') for word in _PERSONAL_WORDS]


def classify_topic(message: str) -> Topic:
    """PERSONAL if the utterance is plausibly about this user's own saved
    data (family, meals, medicines, schedule, appointments, visitors,
    reminders or games); GENERAL otherwise.

    This is a hint for prompt construction only -- see
    ``conversation/prompts.py``'s ``PERSONAL_TOPIC_HINT`` /
    ``GENERAL_TOPIC_HINT``. It never itself decides what the assistant may
    or may not do; the tool registry's own authorization/confirmation gates
    (``tools/registry.py``) are what actually enforce anything.
    """
    query = normalize_text(message)
    if any(phrase in query for phrase in _PERSONAL_PHRASES):
        return 'personal'
    if any(pattern.search(query) for pattern in _WORD_PATTERNS):
        return 'personal'
    return 'general'
