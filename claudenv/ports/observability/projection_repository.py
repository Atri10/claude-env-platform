"""
claude-env :: Ports - Projection repository interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


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
