"""
claude-env :: Ports - Security-scoped audit logger interface
"""
from __future__ import annotations

from typing import Protocol

from claudenv.ports.audit.audit_logger import IAuditLogger


class ISecurityAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to security-relevant events."""
