"""
claude-env :: Ports - Approval gate interface and verdict value object
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from claudenv.domain.value_objects import RequestId, Tier


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
