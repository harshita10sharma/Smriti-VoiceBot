"""Deterministic, language-agnostic utterance validators.

These tables and the ``has_conflicting_utterance`` check were the safety core of
VoiceBot v4.1 (``smriti_voice/intents.py``).  They are moved here **unchanged** so
that the command router and the new conversational layer share one implementation
instead of drifting apart.  ``tests/safety/test_v41_regression.py`` pins the exact
v4.1 behaviour.
"""
from __future__ import annotations

import re

from ..normalize import normalize_text

# Verbs that mutate, destroy or move something.  Their presence next to a
# protected noun vetoes the utterance before any intent matching happens.
CONTRADICTORY_VERBS = {
    'delete', 'remove', 'erase', 'clear', 'drop', 'change', 'modify', 'update', 'edit',
    'cancel', 'stop', 'disable', 'transfer', 'send', 'pay', 'dial',
}

# Nouns that name a protected record or capability.
CONFLICT_TARGETS: dict[str, set[str]] = {
    'medicine': {'medicine', 'medicines', 'medication', 'medications', 'dosage', 'prescription',
                 'tablet', 'tablets', 'pill', 'pills', 'drug', 'drugs'},
    'people': {'people', 'family', 'families', 'person', 'persons', 'caregiver', 'caregivers',
               'relative', 'relatives', 'contact'},
    'play': {'play', 'game', 'games', 'activity', 'activities', 'music'},
    'today': {'today', 'schedule', 'routine', 'day'},
    'help': {'help', 'assist', 'assistance'},
    'call': {'call', 'phone', 'number', 'contact', 'dial'},
    'money': {'money', 'account', 'transfer', 'payment', 'pay'},
}

ALL_CONFLICT_TARGETS: frozenset[str] = frozenset().union(*CONFLICT_TARGETS.values())


def has_conflicting_utterance(text: str) -> bool:
    """True when the utterance asks to mutate, delete or dial something protected.

    Fails closed: any mutating verb co-occurring with a protected noun is vetoed,
    as is any ``call`` that carries digits (an untrusted phone number).
    """
    q = normalize_text(text)
    if not q:
        return False
    toks = set(q.split())
    if 'call' in toks and (re.search(r'\d', q) or any(tok.isdigit() for tok in toks)):
        return True
    if any(verb in toks for verb in CONTRADICTORY_VERBS):
        if any(target in toks for target in ALL_CONFLICT_TARGETS):
            return True
    return False


def contains_phone_number(text: str) -> bool:
    """True when the text contains something that looks like a dialable number."""
    digits = re.sub(r'\D', '', text or '')
    return len(digits) >= 6
