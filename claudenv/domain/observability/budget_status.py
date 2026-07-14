"""
claude-env :: Domain - Budget status enum
"""
from __future__ import annotations

from enum import Enum


class BudgetStatus(str, Enum):
    """Outcome of comparing a repo's month-to-date spend against its budget.

    Ordered by severity for aggregation into an overall worst-case status.
    """

    UNLIMITED = "unlimited"
    OK = "ok"
    WARNING = "warning"
    EXCEEDED = "EXCEEDED"

    @property
    def severity(self) -> int:
        """Higher = worse. Used to fold per-repo statuses into an overall."""
        return {
            BudgetStatus.UNLIMITED: 0,
            BudgetStatus.OK: 0,
            BudgetStatus.WARNING: 1,
            BudgetStatus.EXCEEDED: 2,
        }[self]
