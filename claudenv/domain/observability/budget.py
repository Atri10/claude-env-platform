"""
claude-env :: Domain - Budget evaluation (pure)

Groups the budget domain: BudgetStatus, RepoSpend, RepoBudgetStatus,
BudgetEvaluation, evaluate_budgets.

The advisory monthly-USD budget check. Given month-to-date spend rows and a
budget configuration, decide each repo's status and the overall worst case.

Invariants (matched to docs/guide/observability-budgets.md):
- A budget of 0, absent, or negative means "unlimited" — pct is never computed.
- Thresholds are inclusive: pct >= 1.0 is EXCEEDED; pct >= warn_at is WARNING.
- Budgets are advisory: this function never blocks anything, it only classifies.
"""
from __future__ import annotations

from dataclasses import dataclass
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


@dataclass(frozen=True, slots=True)
class RepoSpend:
    """Month-to-date spend for a single repo (input to budget evaluation)."""

    repo: str
    sessions: int
    input_tokens: int
    output_tokens: int
    spent_usd: float


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


def _classify(spent: float, budget: float, warn_at: float) -> tuple[float | None, BudgetStatus]:
    """Classify one repo's spend. `budget <= 0` short-circuits to unlimited."""
    if budget <= 0:
        return None, BudgetStatus.UNLIMITED
    pct = spent / budget
    if pct >= 1.0:
        return pct, BudgetStatus.EXCEEDED
    if pct >= warn_at:
        return pct, BudgetStatus.WARNING
    return pct, BudgetStatus.OK


def evaluate_budgets(
    spend_rows: list[RepoSpend],
    default_budget: float,
    per_repo_budgets: dict[str, float],
    warn_at: float = 0.8,
) -> BudgetEvaluation:
    """Evaluate every repo's spend against its budget and fold to an overall.

    Pure: no DB, no config file, no clock — the caller supplies spend + config.
    """
    statuses: list[RepoBudgetStatus] = []
    overall = BudgetStatus.OK
    for row in spend_rows:
        budget = per_repo_budgets.get(row.repo, default_budget)
        pct, status = _classify(row.spent_usd or 0.0, budget, warn_at)
        if status.severity > overall.severity:
            overall = status
        statuses.append(
            RepoBudgetStatus(
                repo=row.repo,
                sessions=row.sessions,
                input_tokens=row.input_tokens,
                output_tokens=row.output_tokens,
                spent_usd=row.spent_usd,
                budget_usd=budget if budget > 0 else None,
                pct=round(pct, 3) if pct is not None else None,
                status=status,
            )
        )
    return BudgetEvaluation(warn_at=warn_at, repos=tuple(statuses), overall=overall)
