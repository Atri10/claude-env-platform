"""
claude-env :: Domain - Value Objects - EventType
"""
from __future__ import annotations

from enum import Enum


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
