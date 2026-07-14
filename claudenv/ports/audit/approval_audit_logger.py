"""
claude-env :: Ports - Approval-scoped audit logger interface
"""
from __future__ import annotations

from typing import Protocol

from claudenv.ports.audit.audit_logger import IAuditLogger


class IApprovalAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to human-approval requests and resolutions."""
