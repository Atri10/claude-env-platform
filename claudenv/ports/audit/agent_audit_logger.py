"""
claude-env :: Ports - Agent-scoped audit logger interface
"""
from __future__ import annotations

from typing import Protocol

from claudenv.ports.audit.audit_logger import IAuditLogger


class IAgentAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to agent-initiated actions."""
