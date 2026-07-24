"""
claude-env :: Domain - Value Objects - Enums

Groups: Tier, Action, ApprovalDecision.
"""
from __future__ import annotations

from enum import Enum


class Tier(int, Enum):
    """Privacy/access tiers (0=public, 3=restricted)."""
    PUBLIC = 0
    INTERNAL = 1
    SENSITIVE = 2
    RESTRICTED = 3

    @classmethod
    def from_string(cls, s: str) -> Tier:
        return cls(int(s))

    @property
    def label(self) -> str:
        return {
            Tier.PUBLIC: "public",
            Tier.INTERNAL: "internal",
            Tier.SENSITIVE: "sensitive",
            Tier.RESTRICTED: "restricted",
        }[self]


class Action(str, Enum):
    """Policy decision actions."""
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"


class ApprovalDecision(str, Enum):
    """Human approval decisions."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


