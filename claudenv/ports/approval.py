"""
claude-env :: Ports - Approval Interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from claudenv.domain.value_objects import (
    ApprovalDecision, RequestId, Tier,
)


@dataclass
class GateVerdict:
    """Result of an approval gate evaluation."""
    required: bool
    agent: str
    action: str
    target: str | None
    tier: Tier | None
    reasons: list[str] = ()

    @property
    def summary(self) -> str:
        tgt = f" -> {self.target}" if self.target else ""
        return f"{self.agent}:{self.action}{tgt} (tier={self.tier})"


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


class IApprovalGate(Protocol):
    """Human-in-the-loop approval gate."""

    @abstractmethod
    def evaluate(self, agent: str, action: str, target: str | None = None) -> GateVerdict:
        ...

    @abstractmethod
    def open(self, verdict: GateVerdict) -> RequestId:
        """Persist a pending approval, return its request_id."""
        ...

    @abstractmethod
    def resolve(self, request_id: RequestId, approved: bool, decided_by: str) -> None:
        ...

    @abstractmethod
    def list_open(self) -> list[dict[str, Any]]:
        """Pending approvals, enriched with session_id from audit chain."""
        ...

    @abstractmethod
    def list_recent(self, limit: int = 8) -> list[dict[str, Any]]:
        """Recently resolved approvals (for context in UI)."""
        ...


class IApprovalNotifier(Protocol):
    """Approval notification delivery."""

    @abstractmethod
    def notify(self, request_id: RequestId) -> None:
        ...


class IApprovalUI(Protocol):
    """Approval web UI server."""

    @abstractmethod
    def start(self, port: int | None = None) -> str:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...
