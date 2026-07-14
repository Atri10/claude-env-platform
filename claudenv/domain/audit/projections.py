"""
claude-env :: Domain - Audit Entities - Projections

Groups: ToolCallProjection, AgentActionProjection, RetrievalProjection,
SecurityProjection, PolicyViolationProjection, HumanApprovalProjection,
and the ProjectionBuilder that derives them from an AuditEvent.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from claudenv.domain.audit.events import AuditEvent
from claudenv.domain.value_objects import EventId, RequestId


@dataclass(frozen=True, slots=True)
class ToolCallProjection:
    """Tool call projection for querying."""
    event_id: EventId
    ts: str
    tool: str
    args_json: str
    result_kind: str
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class AgentActionProjection:
    """Agent action projection."""
    event_id: EventId
    ts: str
    agent: str
    action: str
    target: str | None
    summary: str | None
    success: bool


@dataclass(frozen=True, slots=True)
class RetrievalProjection:
    """Retrieval event projection."""
    event_id: EventId
    ts: str
    repo: str
    branch: str | None
    query: str
    top_k: int
    returned: int
    reranked: bool
    duration_ms: int | None


@dataclass(frozen=True, slots=True)
class SecurityProjection:
    """Security event projection."""
    event_id: EventId
    ts: str
    category: str
    severity: str
    detail: str
    source: str | None


@dataclass(frozen=True, slots=True)
class PolicyViolationProjection:
    """Policy violation projection."""
    event_id: EventId
    ts: str
    repo: str
    tier: int | None
    path: str
    rule: str
    decision: str
    actor: str


@dataclass(frozen=True, slots=True)
class HumanApprovalProjection:
    """Human approval projection."""
    request_id: RequestId
    event_id: EventId
    ts: str
    agent: str
    repo: str
    tier: int | None
    action: str
    decision: str
    decided_by: str | None
    decided_at: str | None


class ProjectionBuilder:
    """Build projections from audit events."""

    @staticmethod
    def tool_call(event: AuditEvent) -> ToolCallProjection:
        return ToolCallProjection(
            event_id=event.event_id,
            ts=event.ts,
            tool=event.payload.get("tool", ""),
            args_json=json.dumps(event.payload.get("args", {}), sort_keys=True),
            result_kind=event.payload.get("result_kind", ""),
            duration_ms=event.payload.get("duration_ms"),
        )

    @staticmethod
    def agent_action(event: AuditEvent) -> AgentActionProjection:
        return AgentActionProjection(
            event_id=event.event_id,
            ts=event.ts,
            agent=event.payload.get("agent", ""),
            action=event.payload.get("action", ""),
            target=event.payload.get("target"),
            summary=event.payload.get("summary"),
            success=event.payload.get("success", True),
        )

    @staticmethod
    def retrieval(event: AuditEvent) -> RetrievalProjection:
        return RetrievalProjection(
            event_id=event.event_id,
            ts=event.ts,
            repo=event.payload.get("repo", ""),
            branch=event.payload.get("branch"),
            query=event.payload.get("query", ""),
            top_k=event.payload.get("top_k", 0),
            returned=event.payload.get("returned", 0),
            reranked=event.payload.get("reranked", False),
            duration_ms=event.payload.get("duration_ms"),
        )

    @staticmethod
    def memory_read(event: AuditEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "ts": event.ts,
            "namespace": event.payload.get("namespace"),
            "memory_type": event.payload.get("memory_type"),
            "query": event.payload.get("query"),
            "hit_count": event.payload.get("hit_count", 0),
        }

    @staticmethod
    def memory_write(event: AuditEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "ts": event.ts,
            "namespace": event.payload.get("namespace"),
            "memory_type": event.payload.get("memory_type"),
            "node_id": event.payload.get("node_id"),
            "operation": event.payload.get("operation"),
        }

    @staticmethod
    def security_event(event: AuditEvent) -> SecurityProjection:
        return SecurityProjection(
            event_id=event.event_id,
            ts=event.ts,
            category=event.payload.get("category", ""),
            severity=event.payload.get("severity", ""),
            detail=event.payload.get("detail", ""),
            source=event.payload.get("source"),
        )

    @staticmethod
    def policy_violation(event: AuditEvent) -> PolicyViolationProjection:
        return PolicyViolationProjection(
            event_id=event.event_id,
            ts=event.ts,
            repo=event.payload.get("repo", ""),
            tier=event.payload.get("tier"),
            path=event.payload.get("path", ""),
            rule=event.payload.get("rule", ""),
            decision=event.payload.get("decision", ""),
            actor=event.actor,
        )

    @staticmethod
    def human_approval_request(event: AuditEvent) -> HumanApprovalProjection:
        return HumanApprovalProjection(
            request_id=RequestId.from_string(event.payload.get("request_id", "")),
            event_id=event.event_id,
            ts=event.ts,
            agent=event.payload.get("agent", ""),
            repo=event.payload.get("repo", ""),
            tier=event.payload.get("tier"),
            action=event.payload.get("action", ""),
            decision="pending",
            decided_by=None,
            decided_at=None,
        )

    @staticmethod
    def human_approval_resolve(event: AuditEvent) -> HumanApprovalProjection:
        return HumanApprovalProjection(
            request_id=RequestId.from_string(event.payload.get("request_id", "")),
            event_id=event.event_id,
            ts=event.ts,
            agent=event.payload.get("agent", ""),
            repo=event.payload.get("repo", ""),
            tier=event.payload.get("tier"),
            action=event.payload.get("action", ""),
            decision=event.payload.get("decision", ""),
            decided_by=event.payload.get("decided_by"),
            decided_at=event.payload.get("decided_at"),
        )
