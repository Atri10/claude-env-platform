"""
claude-env :: Ports - Approval Interfaces
"""
from __future__ import annotations

from claudenv.ports.approval.gate_verdict import GateVerdict
from claudenv.ports.approval.approval_repository import IApprovalRepository
from claudenv.ports.approval.approval_gate import IApprovalGate
from claudenv.ports.approval.approval_notifier import IApprovalNotifier
from claudenv.ports.approval.approval_ui import IApprovalUI

__all__ = [
    "GateVerdict",
    "IApprovalRepository",
    "IApprovalGate",
    "IApprovalNotifier",
    "IApprovalUI",
]
