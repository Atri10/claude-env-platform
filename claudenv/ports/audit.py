"""
claude-env :: Ports - Audit Interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from claudenv.domain.value_objects import (
    EventId, EventType, RepoSlug, RequestId, SessionId, Tier,
)
from claudenv.domain.audit import AuditEvent, ChainVerificationResult


class IAuditRepository(Protocol):
    """Audit event persistence."""

    @abstractmethod
    def append(
        self,
        event_type: EventType,
        payload: dict[str, Any],
        session_id: SessionId,
        actor: str,
        repo: RepoSlug | None,
        tier: Tier | None,
        projection: tuple[str, dict[str, Any]] | None = None,
    ) -> EventId:
        ...

    @abstractmethod
    def get_chain(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def verify_chain(self) -> ChainVerificationResult:
        ...


class IAuditLogger(Protocol):
    """High-level audit logging."""

    @abstractmethod
    def tool_call(
        self,
        tool: str,
        args: dict[str, Any],
        result_kind: str,
        duration_ms: int | None = None,
    ) -> EventId:
        ...

    @abstractmethod
    def agent_action(
        self,
        agent: str,
        action: str,
        target: str | None = None,
        summary: str | None = None,
        success: bool = True,
    ) -> EventId:
        ...

    @abstractmethod
    def retrieval(
        self,
        repo: RepoSlug,
        query: str,
        top_k: int,
        returned: int,
        branch: BranchName | None = None,
        max_score: float | None = None,
        min_score: float | None = None,
        reranked: bool = False,
        duration_ms: int | None = None,
    ) -> EventId:
        ...

    @abstractmethod
    def memory_read(
        self,
        namespace: str,
        memory_type: str,
        query: str | None,
        hit_count: int,
    ) -> EventId:
        ...

    @abstractmethod
    def memory_write(
        self,
        namespace: str,
        memory_type: str,
        node_id: NodeId,
        operation: str,
    ) -> EventId:
        ...

    @abstractmethod
    def security_event(
        self,
        category: str,
        severity: str,
        detail: str,
        source: str | None = None,
    ) -> EventId:
        ...

    @abstractmethod
    def policy_violation(
        self,
        path: str,
        rule: str,
        decision: str,
        tier: Tier | None = None,
    ) -> EventId:
        ...

    @abstractmethod
    def human_approval_request(
        self,
        agent: str,
        action: str,
        tier: Tier | None = None,
    ) -> RequestId:
        ...

    @abstractmethod
    def human_approval_resolve(
        self,
        request_id: RequestId,
        decision: str,
        decided_by: str,
    ) -> EventId:
        ...

    @abstractmethod
    def verify_chain(self) -> ChainVerificationResult:
        ...