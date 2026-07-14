"""
claude-env :: Application - Budget service

Advisory monthly-USD budget check. Reads month-to-date spend + budget config,
delegates the decision to the pure domain evaluator, and returns the result.
Never blocks anything — the caller decides what to do with `overall`.
"""
from __future__ import annotations

from datetime import datetime, timezone

from claudenv.domain.observability.budget_evaluator import (
    BudgetEvaluation,
    RepoSpend,
    evaluate_budgets,
)
from claudenv.domain.observability.budget_status import BudgetStatus
from claudenv.ports.observability import IBudgetConfig, IMetricsRepository


def _month_start_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")


class BudgetService:
    """Use-case: evaluate month-to-date spend against configured budgets."""

    def __init__(self, metrics: IMetricsRepository, config: IBudgetConfig):
        self._metrics = metrics
        self._config = config

    def evaluate(self, repo: str | None = None) -> BudgetEvaluation:
        spend = self._metrics.month_to_date_spend(_month_start_iso())
        if repo is not None:
            spend = [s for s in spend if s.repo == repo]
        return evaluate_budgets(
            spend_rows=spend,
            default_budget=self._config.get_default_budget(),
            per_repo_budgets=self._config.get_repo_budgets(),
            warn_at=self._config.get_warn_at(),
        )

    def is_exceeded(self, repo: str | None = None) -> bool:
        """True iff any evaluated repo is EXCEEDED (for CI exit codes)."""
        return self.evaluate(repo).overall is BudgetStatus.EXCEEDED
