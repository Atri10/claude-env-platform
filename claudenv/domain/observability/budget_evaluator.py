"""
claude-env :: Domain - Budget evaluation (pure)

The advisory monthly-USD budget check. Given month-to-date spend rows and a
budget configuration, decide each repo's status and the overall worst case.

Invariants (matched to docs/guide/observability-budgets.md):
- A budget of 0, absent, or negative means "unlimited" — pct is never computed.
- Thresholds are inclusive: pct >= 1.0 is EXCEEDED; pct >= warn_at is WARNING.
- Budgets are advisory: this function never blocks anything, it only classifies.

The value objects live in their own modules; they are re-exported here so
`from claudenv.domain.observability.budget_evaluator import RepoSpend, ...`
remains the stable import surface alongside evaluate_budgets().
"""
from __future__ import annotations

from claudenv.domain.observability.budget_evaluation import BudgetEvaluation
from claudenv.domain.observability.budget_status import BudgetStatus
from claudenv.domain.observability.repo_budget_status import RepoBudgetStatus
from claudenv.domain.observability.repo_spend import RepoSpend

__all__ = ["RepoSpend", "RepoBudgetStatus", "BudgetEvaluation", "evaluate_budgets"]


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
