"""
claude-env :: Ports - Policy Interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from claudenv.domain.value_objects import (
    Action, ApprovalDecision, BranchName, ContentHash, EventId,
    EventType, MemoryType, NodeId, RepoSlug, RequestId, SessionId, Tier,
)
from claudenv.domain.policy import CompiledPolicy, ContentScanResult, PolicyDecision


class IPolicyEngine(Protocol):
    """Policy evaluation engine."""

    @abstractmethod
    def evaluate_path(self, path: str) -> PolicyDecision:
        ...

    @abstractmethod
    def scan_content(self, text: str) -> ContentScanResult:
        ...

    @abstractmethod
    def compile(self, repo_policy: dict, global_policy: dict) -> CompiledPolicy:
        ...


class IPolicyRepository(Protocol):
    """Repository for policy configurations."""

    @abstractmethod
    def get_global_policy(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_repo_policy(self, repo_root: str) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def save_repo_policy(self, repo_root: str, policy: dict[str, Any]) -> None:
        ...


class IPolicySimulator(Protocol):
    """Policy simulation for dry-run testing."""

    @abstractmethod
    def simulate(self, repo_root: str, candidate_policy: dict) -> dict[str, Any]:
        ...

    @abstractmethod
    def diff(self, repo_root: str, candidate_policy: dict) -> dict[str, Any]:
        ...