"""
claude-env :: Ports - Audit Interfaces
"""
from __future__ import annotations

from claudenv.ports.audit.audit_repository import IAuditRepository
from claudenv.ports.audit.audit_logger import IAuditLogger

# ============================================================================
# Role-scoped Audit Logger Ports
# These are distinct interface types (all backed by the same SqliteAuditLogger)
# so the DI container can register and resolve them independently. Each is
# structurally identical to IAuditLogger; the separation exists to make the
# caller's intent (tool vs agent vs security vs approval) explicit in the type.
# ============================================================================
from claudenv.ports.audit.tool_audit_logger import IToolAuditLogger
from claudenv.ports.audit.agent_audit_logger import IAgentAuditLogger
from claudenv.ports.audit.security_audit_logger import ISecurityAuditLogger
from claudenv.ports.audit.approval_audit_logger import IApprovalAuditLogger
from claudenv.ports.audit.verifiable_ledger import IVerifiableLedger

__all__ = [
    "IAuditRepository",
    "IAuditLogger",
    "IToolAuditLogger",
    "IAgentAuditLogger",
    "ISecurityAuditLogger",
    "IApprovalAuditLogger",
    "IVerifiableLedger",
]
