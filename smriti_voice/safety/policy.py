"""The deterministic safety policy.

Nothing in this module consults a model.  It is pure text matching over a
config-driven table, so its verdicts are reproducible and testable.  The
conversation manager calls :meth:`SafetyPolicy.screen_utterance` *before* the LLM
sees the turn, and :meth:`SafetyPolicy.screen_tool_call` *after* the LLM proposes
a tool but *before* anything executes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from ..normalize import normalize_text
from ..schemas import SafetyDecision, SafetyLevel
from .validators import contains_phone_number, has_conflicting_utterance

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY_PATH = ROOT / 'config' / 'safety.json'

# Spoken refusals.  Short, calm, and they always name the human who can help.
REFUSALS: dict[str, dict[str, str]] = {
    'medication': {
        'eng': "I am not able to change anything about your medicines. That has to be done by your "
               "doctor or your caregiver. I can read out your medicine schedule if you would like.",
        'hin': "मैं आपकी दवा में कोई बदलाव नहीं कर सकती। यह आपके डॉक्टर या देखभाल करने वाले को करना होगा। "
               "मैं आपकी दवा का समय बता सकती हूँ।",
        'asm': "মই আপোনাৰ ঔষধত কোনো সলনি কৰিব নোৱাৰো। ই আপোনাৰ ডাক্তৰ বা যত্ন লোৱা জনে কৰিব লাগিব।",
        'ben': "আমি আপনার ওষুধে কোনো পরিবর্তন করতে পারি না। এটি আপনার ডাক্তার বা পরিচর্যাকারীকে করতে হবে।",
    },
    'financial': {
        'eng': "I cannot send money or make payments. Please ask a family member you trust to help "
               "you with that.",
        'hin': "मैं पैसे नहीं भेज सकती और न ही कोई भुगतान कर सकती हूँ। कृपया अपने किसी विश्वसनीय परिजन से मदद लें।",
        'asm': "মই ধন পঠিয়াব বা কোনো পৰিশোধ কৰিব নোৱাৰো। অনুগ্ৰহ কৰি বিশ্বাসী পৰিয়ালৰ সহায় লওক।",
        'ben': "আমি টাকা পাঠাতে বা কোনো পেমেন্ট করতে পারি না। অনুগ্রহ করে বিশ্বস্ত পরিবারের সাহায্য নিন।",
    },
    'record_deletion': {
        'eng': "I am not able to delete your saved information. If something needs to be removed, "
               "your caregiver can do it for you.",
        'hin': "मैं आपकी सहेजी हुई जानकारी नहीं हटा सकती। अगर कुछ हटाना है तो आपके देखभाल करने वाले कर देंगे।",
        'asm': "মই আপোনাৰ সংৰক্ষিত তথ্য মচিব নোৱাৰো। প্ৰয়োজন হ'লে আপোনাৰ যত্ন লোৱা জনে কৰি দিব।",
        'ben': "আমি আপনার সংরক্ষিত তথ্য মুছতে পারি না। প্রয়োজন হলে আপনার পরিচর্যাকারী করে দেবেন।",
    },
    'unknown_number': {
        'eng': "I can only call the people saved in your family list. I am not able to dial a number "
               "that I do not know.",
        'hin': "मैं केवल आपकी परिवार सूची में सहेजे लोगों को ही फ़ोन कर सकती हूँ। अनजान नंबर पर कॉल नहीं कर सकती।",
        'asm': "মই কেৱল আপোনাৰ পৰিয়ালৰ তালিকাত থকা মানুহক ফোন কৰিব পাৰো। অচিনাকি নম্বৰলৈ নহয়।",
        'ben': "আমি শুধু আপনার পরিবারের তালিকায় থাকা মানুষকে ফোন করতে পারি। অচেনা নম্বরে নয়।",
    },
    'caregiver_permissions': {
        'eng': "Those settings are looked after by your caregiver. I am not able to change them.",
        'hin': "ये सेटिंग्स आपके देखभाल करने वाले संभालते हैं। मैं इन्हें नहीं बदल सकती।",
        'asm': "এই ছেটিংবোৰ আপোনাৰ যত্ন লোৱা জনে চায়। মই সলনি কৰিব নোৱাৰো।",
        'ben': "এই সেটিংস আপনার পরিচর্যাকারী দেখেন। আমি এগুলি পরিবর্তন করতে পারি না।",
    },
    'safety_override': {
        'eng': "I have to keep my safety rules on. I can still help you with your family, your day, "
               "your medicine times, or a game.",
        'hin': "मुझे अपने सुरक्षा नियम चालू रखने होंगे। मैं आपके परिवार, आपके दिन, दवा के समय या खेल में मदद कर सकती हूँ।",
        'asm': "মই মোৰ সুৰক্ষা নিয়ম বন্ধ কৰিব নোৱাৰো। পৰিয়াল, দিনৰ কাম বা খেলত সহায় কৰিব পাৰো।",
        'ben': "আমি আমার নিরাপত্তা নিয়ম বন্ধ করতে পারি না। পরিবার, দিনের কাজ বা খেলায় সাহায্য করতে পারি।",
    },
    'generic': {
        'eng': "I am not able to do that. Please ask your caregiver to help you with it.",
        'hin': "मैं यह नहीं कर सकती। कृपया अपने देखभाल करने वाले से मदद लें।",
        'asm': "মই সেইটো কৰিব নোৱাৰো। অনুগ্ৰহ কৰি যত্ন লোৱা জনৰ সহায় লওক।",
        'ben': "আমি এটি করতে পারি না। অনুগ্রহ করে আপনার পরিচর্যাকারীর সাহায্য নিন।",
    },
}


def refusal_text(refusal_key: str | None, language: str) -> str:
    """Refusal in the user's language, falling back to English (never silent)."""
    table = REFUSALS.get(refusal_key or 'generic', REFUSALS['generic'])
    return table.get(language) or table['eng']


@dataclass(frozen=True)
class _Category:
    name: str
    refusal_key: str
    verbs: frozenset[str]
    nouns: frozenset[str]
    standalone: tuple[str, ...]


class SafetyPolicy:
    """Config-driven, deterministic. Fails closed on anything it cannot parse."""

    def __init__(self, policy_path: Path | None = None) -> None:
        self.path = Path(policy_path or DEFAULT_POLICY_PATH)
        raw = json.loads(self.path.read_text(encoding='utf-8'))
        self._categories = tuple(
            _Category(
                name=name,
                refusal_key=spec.get('refusal_key', name),
                verbs=frozenset(spec.get('verbs', [])),
                nouns=frozenset(spec.get('nouns', [])),
                standalone=tuple(normalize_text(p) for p in spec.get('standalone', [])),
            )
            for name, spec in raw.get('sensitive_categories', {}).items()
        )
        self.confirmation_required_actions = frozenset(raw.get('confirmation_required_actions', []))
        self._affirmations = {
            lang: frozenset(normalize_text(w) for w in words)
            for lang, words in raw.get('affirmations', {}).items()
        }
        self._negations = {
            lang: frozenset(normalize_text(w) for w in words)
            for lang, words in raw.get('negations', {}).items()
        }

    # ------------------------------------------------------------------ #
    # Utterance screening (runs before the LLM)
    # ------------------------------------------------------------------ #
    def screen_utterance(self, text: str) -> SafetyDecision:
        """Refuse anything that asks to mutate a protected record or dial a number."""
        q = normalize_text(text)
        if not q:
            return SafetyDecision(allowed=True, reason='empty')
        tokens = set(q.split())

        for category in self._categories:
            for phrase in category.standalone:
                if phrase and phrase in q:
                    return SafetyDecision(allowed=False, reason='sensitive_phrase',
                                          category=category.name, refusal_key=category.refusal_key,
                                          matched=[phrase])
            verb_hits = sorted(tokens & category.verbs)
            noun_hits = sorted(tokens & category.nouns)
            if verb_hits and noun_hits:
                return SafetyDecision(allowed=False, reason='sensitive_verb_noun_pair',
                                      category=category.name, refusal_key=category.refusal_key,
                                      matched=verb_hits + noun_hits)

        # A call carrying digits is an untrusted number, whatever else it says.
        if ('call' in tokens or 'dial' in tokens or 'phone' in tokens) and contains_phone_number(q):
            return SafetyDecision(allowed=False, reason='phone_number_in_utterance',
                                 category='unknown_number', refusal_key='unknown_number',
                                 matched=['digits'])

        # Keep the v4.1 veto as a second, independent gate.
        if has_conflicting_utterance(q):
            return SafetyDecision(allowed=False, reason='unsafe_conflicting_request',
                                 category='record_deletion', refusal_key='generic')

        return SafetyDecision(allowed=True, reason='clear')

    # ------------------------------------------------------------------ #
    # Tool screening (runs after the LLM proposes, before execution)
    # ------------------------------------------------------------------ #
    def screen_tool_call(self, tool_name: str, arguments: dict, safety_level: SafetyLevel) -> SafetyDecision:
        if safety_level is SafetyLevel.SENSITIVE:
            return SafetyDecision(allowed=False, reason='sensitive_tool',
                                  category='sensitive_tool', refusal_key='generic',
                                  matched=[tool_name])
        # Argument values are attacker-controlled text; screen them too.
        for key, value in (arguments or {}).items():
            if isinstance(value, str):
                verdict = self.screen_utterance(value)
                if not verdict.allowed:
                    return SafetyDecision(allowed=False, reason=f'unsafe_argument:{key}',
                                          category=verdict.category, refusal_key=verdict.refusal_key,
                                          matched=verdict.matched)
        return SafetyDecision(allowed=True, reason='clear')

    # ------------------------------------------------------------------ #
    # Confirmation vocabulary
    # ------------------------------------------------------------------ #
    def is_affirmation(self, text: str, language: str = 'eng') -> bool:
        q = normalize_text(text)
        if not q or len(q.split()) > 4:
            return False
        vocab = self._affirmations.get(language, frozenset()) | self._affirmations.get('eng', frozenset())
        return q in vocab or any(q == word for word in vocab)

    def is_negation(self, text: str, language: str = 'eng') -> bool:
        q = normalize_text(text)
        if not q or len(q.split()) > 4:
            return False
        vocab = self._negations.get(language, frozenset()) | self._negations.get('eng', frozenset())
        return q in vocab

    def requires_confirmation(self, action: str) -> bool:
        return action in self.confirmation_required_actions


@lru_cache(maxsize=4)
def get_policy(policy_path: str | None = None) -> SafetyPolicy:
    return SafetyPolicy(Path(policy_path) if policy_path else None)
