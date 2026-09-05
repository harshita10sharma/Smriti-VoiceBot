"""Prompt-injection detection.

Two separate jobs:

* :func:`screen_user_input` — an elderly user does not normally say "ignore all
  previous instructions".  When they do, it is either an attack or confusion, and
  either way it must not reach the model as an instruction.
* :func:`sanitise_untrusted` — text that came from the database (caregiver notes,
  memory descriptions) or a tool result is *data*.  It is wrapped so the model
  cannot mistake it for a system instruction.
"""
from __future__ import annotations

import re

from ..normalize import normalize_text

INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ('instruction_override', re.compile(
        r'\b(ignore|disregard|forget|override|bypass)\b.{0,30}\b'
        r'(previous|prior|earlier|above|all|your|the)\b.{0,20}'
        r'(instruction|instructions|rule|rules|prompt|prompts|direction|directions|guideline|guidelines)',
        re.IGNORECASE)),
    ('safety_disable', re.compile(
        r'\b(disable|turn\s+off|switch\s+off|remove|deactivate|bypass|skip)\b.{0,20}'
        r'\b(safety|safeguard|safeguards|guardrail|guardrails|restriction|restrictions|filter|filters)\b',
        re.IGNORECASE)),
    ('role_hijack', re.compile(
        r'\b(you\s+are\s+now|act\s+as|pretend\s+to\s+be|from\s+now\s+on\s+you|'
        r'developer\s+mode|jailbreak|dan\s+mode|sudo\s+mode|root\s+mode)\b',
        re.IGNORECASE)),
    ('fake_authority', re.compile(
        r'\b(system\s*(message|prompt|says|instruction)|'
        r'(the\s+)?(caregiver|doctor|nurse|admin|administrator|developer|previous\s+assistant)\s+'
        r'(said|says|told|authorou?ized|authorised|authorized|approved|permits?|allows?))\b',
        re.IGNORECASE)),
    ('fake_turn', re.compile(
        r'(^|\n)\s*(system|assistant|developer)\s*:', re.IGNORECASE)),
    ('markup_injection', re.compile(
        r'<\s*/?\s*(system|instruction|tool_call|function_call|im_start|im_end)\b', re.IGNORECASE)),
    ('tool_forcing', re.compile(
        r'\b(call|invoke|execute|run)\s+the?\s*(tool|function|api)\b', re.IGNORECASE)),
)


def detect(text: str) -> list[str]:
    """Return the names of every injection pattern found in ``text``."""
    if not text:
        return []
    return [name for name, pattern in INJECTION_PATTERNS if pattern.search(text)]


def screen_user_input(text: str) -> tuple[bool, list[str]]:
    """``(is_suspicious, matched_patterns)`` for something the user said."""
    matches = detect(text)
    return bool(matches), matches


def sanitise_untrusted(text: str, *, max_chars: int = 4000) -> str:
    """Neutralise stored/tool text before it is shown to the model.

    Injection markers are defanged rather than deleted, so a caregiver note that
    innocently contains one still reads correctly to the user.
    """
    if not text:
        return ''
    cleaned = str(text)[:max_chars]
    cleaned = re.sub(r'<\s*/?\s*(system|instruction|tool_call|function_call|im_start|im_end)\b',
                     '[markup]', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'(^|\n)\s*(system|assistant|developer)\s*:', r'\1[text] ', cleaned,
                     flags=re.IGNORECASE)
    for name, pattern in INJECTION_PATTERNS:
        if name in {'markup_injection', 'fake_turn'}:
            continue
        cleaned = pattern.sub('[removed instruction-like text]', cleaned)
    return cleaned


def looks_like_elderly_confusion(text: str) -> bool:
    """Distinguish a confused repeat from an attack, so we answer kindly."""
    q = normalize_text(text)
    return bool(q) and len(q.split()) <= 3 and not detect(text)
