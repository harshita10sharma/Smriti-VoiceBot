"""Deterministic safety layer.  The model proposes; this package decides."""
from .authorization import Authorizer, Principal
from .confirmation import ConfirmationManager, cancellation_text, prompt_for
from .policy import SafetyPolicy, get_policy, refusal_text
from .prompt_injection import detect, sanitise_untrusted, screen_user_input
from .validators import (
    CONFLICT_TARGETS,
    CONTRADICTORY_VERBS,
    contains_phone_number,
    has_conflicting_utterance,
)

__all__ = [
    'Authorizer', 'Principal', 'ConfirmationManager', 'cancellation_text', 'prompt_for',
    'SafetyPolicy', 'get_policy', 'refusal_text', 'detect', 'sanitise_untrusted',
    'screen_user_input', 'CONFLICT_TARGETS', 'CONTRADICTORY_VERBS',
    'contains_phone_number', 'has_conflicting_utterance',
]
