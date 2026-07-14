"""
claude-env :: Ports - Approval Interfaces
"""
from __future__ import annotations

from claudenv.ports.approval.gate import GateVerdict, IApprovalGate
from claudenv.ports.approval.interfaces import (
    IApprovalRepository,
    IApprovalNotifier,
    IApprovalUI,
)

__all__ = [
    "GateVerdict",
    "IApprovalRepository",
    "IApprovalGate",
    "IApprovalNotifier",
    "IApprovalUI",
]
