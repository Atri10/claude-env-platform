"""
claude-env :: Domain - Observability

Pure domain logic for the operations feature set: budget evaluation, retrieval
feedback boosting, and dashboard math. No I/O, no framework, no ports — these
are deterministic functions and value objects the application layer composes
with repositories.
"""
from __future__ import annotations

from claudenv.domain.observability.budget import (
    BudgetEvaluation,
    BudgetStatus,
    RepoBudgetStatus,
    RepoSpend,
    evaluate_budgets,
)
from claudenv.domain.observability.metrics import (
    BOOST_CAP,
    BOOST_UNIT,
    nearest_rank_percentile,
    parse_window_days,
    query_hash,
    usage_boost,
    usage_boosts_from_counts,
)

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
