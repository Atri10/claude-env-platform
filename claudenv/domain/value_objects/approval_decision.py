"""
claude-env :: Domain - Value Objects - ApprovalDecision
"""
from __future__ import annotations

from enum import Enum


class ApprovalDecision(str, Enum):
    """Human approval decisions."""
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
