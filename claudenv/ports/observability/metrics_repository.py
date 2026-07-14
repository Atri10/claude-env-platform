"""
claude-env :: Ports - Metrics repository interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.observability.budget_evaluator import RepoSpend


class IMetricsRepository(Protocol):
    """Read access to the observability metrics tables."""

    @abstractmethod
    def month_to_date_spend(self, month_start_iso: str) -> list[RepoSpend]:
        ...

    @abstractmethod
    def top_session_costs(self, since_iso: str, limit: int = 10) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def latency_by_component(self, since_iso: str) -> dict[str, list[float]]:
        ...

    @abstractmethod
    def retrieval_quality_by_repo(self, since_iso: str) -> list[dict[str, Any]]:
        ...
