"""
claude-env :: Ports - Observability interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.observability.budget import RepoSpend


class IBudgetConfig(Protocol):
    """Budget configuration (config/budgets.yaml)."""

    @abstractmethod
    def get_warn_at(self) -> float:
        ...

    @abstractmethod
    def get_default_budget(self) -> float:
        ...

    @abstractmethod
    def get_repo_budgets(self) -> dict[str, float]:
        ...


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


class IFeedbackRepository(Protocol):
    """Read/write access to rag_chunk_feedback."""

    @abstractmethod
    def record_retrieved(
        self,
        repo: str,
        branch: str,
        query_hash: str,
        chunks: list[dict[str, Any]],
        session_id: str,
    ) -> None:
        ...

    @abstractmethod
    def used_counts(self, repo: str, branch: str) -> dict[str, int]:
        ...

    @abstractmethod
    def signal_totals(self, repo: str | None = None) -> dict[str, int]:
        ...

    @abstractmethod
    def top_used_files(self, repo: str | None = None, limit: int = 10) -> list[tuple[str, int]]:
        ...


class IProjectionRepository(Protocol):
    """Read access to audit projections surfaced on the dashboard."""

    @abstractmethod
    def recent_policy_violations(self, limit: int = 10) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def security_event_breakdown(self, since_iso: str) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def open_approvals(self) -> list[dict[str, Any]]:
        ...
