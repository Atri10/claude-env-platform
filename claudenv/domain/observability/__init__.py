"""
claude-env :: Domain - Observability

Pure domain logic for the operations feature set: budget evaluation, retrieval
feedback boosting, and dashboard math. No I/O, no framework, no ports — these
are deterministic functions and value objects the application layer composes
with repositories.
"""
from __future__ import annotations

from claudenv.domain.observability.boost_calculator import (
    BOOST_CAP,
    BOOST_UNIT,
    usage_boost,
    usage_boosts_from_counts,
)
from claudenv.domain.observability.budget_evaluation import BudgetEvaluation
from claudenv.domain.observability.budget_evaluator import evaluate_budgets
from claudenv.domain.observability.budget_status import BudgetStatus
from claudenv.domain.observability.repo_budget_status import RepoBudgetStatus
from claudenv.domain.observability.repo_spend import RepoSpend
from claudenv.domain.observability.percentile import nearest_rank_percentile
from claudenv.domain.observability.query_hash import query_hash
from claudenv.domain.observability.window import parse_window_days

__all__ = [
    "BudgetStatus",
    "RepoSpend",
    "RepoBudgetStatus",
    "BudgetEvaluation",
    "evaluate_budgets",
    "BOOST_UNIT",
    "BOOST_CAP",
    "usage_boost",
    "usage_boosts_from_counts",
    "nearest_rank_percentile",
    "parse_window_days",
    "query_hash",
]
