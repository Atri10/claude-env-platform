"""
claude-env :: Ports - Audit logger interfaces

Role-scoped Audit Logger Ports: IToolAuditLogger, IAgentAuditLogger,
ISecurityAuditLogger, IApprovalAuditLogger, and IVerifiableLedger are
distinct interface types (all backed by the same SqliteAuditLogger) so the
DI container can register and resolve them independently. Each is
structurally identical to IAuditLogger; the separation exists to make the
caller's intent (tool vs agent vs security vs approval) explicit in the type.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.audit import ChainVerificationResult
from claudenv.domain.value_objects import (
    BranchName,
    EventId,
    NodeId,
    RepoSlug,
    RequestId,
    Tier,
)


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


class IToolAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to native tool calls (Read/Write/Edit/Bash)."""


class IAgentAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to agent-initiated actions."""


class ISecurityAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to security-relevant events."""


class IApprovalAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to human-approval requests and resolutions."""


class IVerifiableLedger(IAuditLogger, Protocol):
    """Audit ledger that can cryptographically verify its own hash chain."""
