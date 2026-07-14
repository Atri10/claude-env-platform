"""
claude-env :: Domain - Policy Engine - PolicyDecision
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import Action


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of a policy evaluation."""
    action: Action
    reason: str
    rule: str = ""

    @classmethod
    def allow(cls, reason: str = "no blocking rule") -> PolicyDecision:
        return cls(Action.ALLOW, reason)

    @classmethod
    def block(cls, reason: str, rule: str = "") -> PolicyDecision:
        return cls(Action.BLOCK, reason, rule)

    @classmethod
    def redact(cls, reason: str, rule: str = "") -> PolicyDecision:
        return cls(Action.REDACT, reason, rule)

    @property
    def is_allowed(self) -> bool:
        return self.action == Action.ALLOW

    @property
    def is_blocked(self) -> bool:
        return self.action == Action.BLOCK

    @property
    def is_redacted(self) -> bool:
        return self.action == Action.REDACT
