"""Who may run what.

Authorisation is checked on every tool call, after schema validation and before
execution.  It is deliberately boring: a table lookup and a user-id comparison.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..schemas import PermissionLevel, SafetyDecision, SafetyLevel


@dataclass(frozen=True)
class Principal:
    """The identity a turn runs as.  Voice turns are always ``USER``."""
    user_id: str
    level: PermissionLevel = PermissionLevel.USER

    def can(self, required: PermissionLevel) -> bool:
        order = {PermissionLevel.USER: 0, PermissionLevel.CAREGIVER: 1, PermissionLevel.SYSTEM: 2}
        return order[self.level] >= order[required]


class Authorizer:
    """Fails closed: an unknown permission or a mismatched user id is a refusal."""

    def authorize(
        self,
        principal: Principal,
        *,
        tool_name: str,
        required_permission: PermissionLevel,
        safety_level: SafetyLevel,
        target_user_id: str | None = None,
    ) -> SafetyDecision:
        if safety_level is SafetyLevel.SENSITIVE:
            return SafetyDecision(allowed=False, reason='sensitive_action_requires_caregiver',
                                  category='authorization', refusal_key='generic', matched=[tool_name])
        if not principal.can(required_permission):
            return SafetyDecision(allowed=False, reason='insufficient_permission',
                                  category='authorization', refusal_key='caregiver_permissions',
                                  matched=[tool_name, required_permission.value])
        # A voice turn may only ever touch its own user's data.
        if target_user_id is not None and target_user_id != principal.user_id:
            return SafetyDecision(allowed=False, reason='cross_user_access_denied',
                                  category='authorization', refusal_key='generic',
                                  matched=[tool_name])
        return SafetyDecision(allowed=True, reason='authorized')
