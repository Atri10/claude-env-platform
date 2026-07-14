"""
claude-env :: Domain - Value Objects - Enums

Groups: Tier, Action, ApprovalDecision, EventType.
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


class EventType(str, Enum):
    """Audit event types."""
    AGENT_ACTION = "agent_action"
    TOOL_CALL = "tool_call"
    RETRIEVAL = "retrieval"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    SECURITY_EVENT = "security_event"
    POLICY_VIOLATION = "policy_violation"
    HUMAN_APPROVAL_REQUEST = "human_approval_request"
    HUMAN_APPROVAL_RESOLVE = "human_approval_resolve"
