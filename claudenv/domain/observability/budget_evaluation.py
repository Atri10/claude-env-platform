"""
claude-env :: Domain - Aggregate budget evaluation across all repos
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.observability.budget_status import BudgetStatus
from claudenv.domain.observability.repo_budget_status import RepoBudgetStatus


@dataclass(frozen=True, slots=True)
class BudgetEvaluation:
    """Aggregate budget evaluation across all repos."""

    warn_at: float
    repos: tuple[RepoBudgetStatus, ...]
    overall: BudgetStatus

    def to_dict(self) -> dict:
        return {
            "warn_at": self.warn_at,
            "repos": [r.to_dict() for r in self.repos],
            "overall": self.overall.value,
        }
