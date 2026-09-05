"""Two-step confirmation for controlled actions.

A controlled action (placing a call to a trusted contact, creating a reminder,
starting a game) is never executed on the strength of one utterance.  The manager
stores a :class:`~smriti_voice.schemas.PendingConfirmation` and executes only when
the next turn is an explicit affirmation, in the user's own language.
"""
from __future__ import annotations

from ..schemas import PendingConfirmation
from .policy import SafetyPolicy

_CALL_PROMPT = {
    'eng': 'Would you like me to call {name} now? Please say yes or no.',
    'hin': 'क्या मैं अभी {name} को फ़ोन करूँ? कृपया हाँ या नहीं कहिए।',
    'asm': 'মই এতিয়া {name}ক ফোন কৰিম নেকি? অনুগ্ৰহ কৰি হয় বা নহয় কওক।',
    'ben': 'আমি কি এখন {name}-কে ফোন করব? অনুগ্রহ করে হ্যাঁ বা না বলুন।',
}

CONFIRMATION_PROMPTS: dict[str, dict[str, str]] = {
    # Both call actions share wording: the user hears a name, not an action code.
    'CALL_PRIMARY_CONTACT': _CALL_PROMPT,
    'CALL_BINA': {
        'eng': 'Would you like me to call {name} now? Please say yes or no.',
        'hin': 'क्या मैं अभी {name} को फ़ोन करूँ? कृपया हाँ या नहीं कहिए।',
        'asm': 'মই এতিয়া {name}ক ফোন কৰিম নেকি? অনুগ্ৰহ কৰি হয় বা নহয় কওক।',
        'ben': 'আমি কি এখন {name}-কে ফোন করব? অনুগ্রহ করে হ্যাঁ বা না বলুন।',
    },
    'CREATE_REMINDER': {
        'eng': 'Shall I save a reminder for {name}? Please say yes or no.',
        'hin': 'क्या मैं {name} के लिए याद दिलाने वाला सेट कर दूँ? हाँ या नहीं कहिए।',
        'asm': '{name}ৰ বাবে এটা মনত পেলোৱা ৰাখিম নেকি? হয় বা নহয় কওক।',
        'ben': '{name}-এর জন্য একটি রিমাইন্ডার রাখব কি? হ্যাঁ বা না বলুন।',
    },
    'START_GAME': {
        'eng': 'Shall I start {name} for you? Please say yes or no.',
        'hin': 'क्या मैं आपके लिए {name} शुरू करूँ? हाँ या नहीं कहिए।',
        'asm': 'মই আপোনাৰ বাবে {name} আৰম্ভ কৰিম নেকি? হয় বা নহয় কওক।',
        'ben': 'আমি কি আপনার জন্য {name} শুরু করব? হ্যাঁ বা না বলুন।',
    },
}

CANCELLED: dict[str, str] = {
    'eng': 'That is fine. I have not done anything. Tell me whenever you are ready.',
    'hin': 'कोई बात नहीं। मैंने कुछ नहीं किया। जब आप तैयार हों तब बता दीजिए।',
    'asm': 'ঠিক আছে। মই একো কৰা নাই। যেতিয়া প্ৰস্তুত হ\'ব তেতিয়া ক\'ব।',
    'ben': 'ঠিক আছে। আমি কিছু করিনি। যখন প্রস্তুত হবেন তখন বলবেন।',
}


GENERIC_PROMPT: dict[str, str] = {
    'eng': 'Shall I do that now? Please say yes or no.',
    'hin': 'क्या मैं यह अभी करूँ? कृपया हाँ या नहीं कहिए।',
    'asm': 'মই এতিয়া সেইটো কৰিম নেকি? হয় বা নহয় কওক।',
    'ben': 'আমি কি এখন সেটি করব? হ্যাঁ বা না বলুন।',
}


def prompt_for(action: str, language: str, name: str = '') -> str:
    """The spoken question, in the user's language, naming what will happen."""
    table = CONFIRMATION_PROMPTS.get(action) or GENERIC_PROMPT
    template = table.get(language) or table['eng']
    return template.format(name=name or 'that')


def cancellation_text(language: str) -> str:
    return CANCELLED.get(language) or CANCELLED['eng']


class ConfirmationManager:
    """Holds at most one pending confirmation per session and ages it out."""

    def __init__(self, policy: SafetyPolicy) -> None:
        self.policy = policy

    def create(self, *, action: str, language: str, tool_name: str | None = None,
               arguments: dict | None = None, name: str = '') -> PendingConfirmation:
        return PendingConfirmation(
            action=action,
            tool_name=tool_name,
            arguments=arguments or {},
            prompt=prompt_for(action, language, name),
            language=language,
        )

    def resolve(self, pending: PendingConfirmation, utterance: str,
                language: str | None = None) -> str:
        """``'confirmed'`` | ``'cancelled'`` | ``'unclear'``.  Silence never confirms."""
        lang = language or pending.language
        if self.policy.is_negation(utterance, lang):
            return 'cancelled'
        if self.policy.is_affirmation(utterance, lang):
            return 'confirmed'
        return 'unclear'
