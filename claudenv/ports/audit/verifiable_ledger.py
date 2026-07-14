"""
claude-env :: Ports - Verifiable audit ledger interface
"""
from __future__ import annotations

from typing import Protocol

from claudenv.ports.audit.audit_logger import IAuditLogger


class IVerifiableLedger(IAuditLogger, Protocol):
    """Audit ledger that can cryptographically verify its own hash chain."""
