"""
claude-env :: Domain - Per-repo budget evaluation result
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.observability.budget_status import BudgetStatus


@dataclass(frozen=True, slots=True)
class RepoBudgetStatus:
    """Per-repo budget evaluation result."""

    repo: str
    sessions: int
    input_tokens: int
    output_tokens: int
    spent_usd: float
    budget_usd: float | None
    pct: float | None
    status: BudgetStatus

    def to_dict(self) -> dict:
        return {
            "repo": self.repo,
            "sessions": self.sessions,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "spent_usd": self.spent_usd,
            "budget_usd": self.budget_usd,
            "pct": self.pct,
            "status": self.status.value,
        }
