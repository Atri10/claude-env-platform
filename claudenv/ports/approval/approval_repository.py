"""
claude-env :: Ports - Approval repository interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.value_objects import ApprovalDecision, RequestId, Tier


class IApprovalRepository(Protocol):
    """Approval request persistence."""

    @abstractmethod
    def insert(self, request_id: RequestId, agent: str, action: str, tier: Tier | None) -> None:
        ...

    @abstractmethod
    def get(self, request_id: RequestId) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def update_decision(self, request_id: RequestId, decision: ApprovalDecision, decided_by: str) -> None:
        ...

    @abstractmethod
    def list_open(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def list_recent(self, limit: int = 8) -> list[dict[str, Any]]:
        ...
