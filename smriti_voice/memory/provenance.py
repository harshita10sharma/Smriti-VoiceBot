"""Trust rules for stored facts.

The rule the specification asks for — "AI-generated text must NOT automatically
become trusted memory" — is enforced here and used by every write path.
"""
from __future__ import annotations

from .models import Provenance

# Only a caregiver (or a seeded/imported record vouched for by one) is trusted.
TRUSTED_SOURCES = frozenset({'caregiver', 'import', 'seed'})


def provenance_for_write(*, source: str, created_by: str | None = None,
                         confidence: float = 1.0) -> Provenance:
    """Build provenance for a new row, forcing anything AI-written to unverified."""
    trusted = source in TRUSTED_SOURCES
    return Provenance(
        source=source,  # type: ignore[arg-type]
        created_by=created_by,
        confidence=confidence if trusted else min(confidence, 0.5),
        verification_status='verified' if trusted else 'unverified',
    )


def is_quotable(provenance: Provenance) -> bool:
    """May this fact be stated to the user as established truth?"""
    return provenance.verification_status == 'verified'


def caveat_for(provenance: Provenance, language: str = 'eng') -> str | None:
    """A short caveat the model must include when reading out an unverified fact."""
    if is_quotable(provenance):
        return None
    return {
        'eng': 'This was noted but has not been confirmed by your caregiver yet.',
        'hin': 'यह लिखा गया था, पर आपके देखभाल करने वाले ने अभी इसकी पुष्टि नहीं की है।',
        'asm': 'এইটো লিখা হৈছিল, কিন্তু এতিয়াও নিশ্চিত কৰা হোৱা নাই।',
        'ben': 'এটি লেখা হয়েছিল, তবে এখনও নিশ্চিত করা হয়নি।',
    }.get(language, 'This has not been confirmed by your caregiver yet.')
