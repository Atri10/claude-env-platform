"""
claude-env :: Domain - Audit Entities
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from claudenv.domain.value_objects import (
    EventId, RequestId, RepoSlug, SessionId, Tier, iso_now,
)

GENESIS = "GENESIS"

# Envelope format version. Bump when the canonical-payload shape changes so
# historical rows stay verifiable under the schema they were written with.
ENVELOPE_SCHEMA_VERSION = 2


class EventType(str, Enum):
    """Audit event types."""
    TOOL_CALL = "tool_call"
    AGENT_ACTION = "agent_action"
    RETRIEVAL = "retrieval"
    MEMORY_READ = "memory_read"
    MEMORY_WRITE = "memory_write"
    SECURITY_EVENT = "security_event"
    POLICY_VIOLATION = "policy_violation"
    HUMAN_APPROVAL_REQUEST = "human_approval_request"
    HUMAN_APPROVAL_RESOLVE = "human_approval_resolve"


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Immutable audit event with hash chain.

    schema_version/host/pid/request_id are governance trace metadata: they
    are folded into the hashed envelope like every other field, so tampering
    with *where* or *under what correlation* an event happened is caught by
    chain verification exactly like tampering with the event body is.
    """
    event_id: EventId
    ts: str  # ISO 8601
    event_type: EventType
    actor: str
    session_id: SessionId
    repo: RepoSlug | None
    tier: Tier | None
    payload: dict[str, Any]
    prev_hash: str
    event_hash: str
    schema_version: int = ENVELOPE_SCHEMA_VERSION
    host: str | None = None
    pid: int | None = None
    request_id: str | None = None

    def canonical_payload(self) -> str:
        """Canonical JSON for hashing — the exact bytes that must be persisted
        verbatim, since verification replays this string rather than
        re-deriving it from separately stored columns."""
        return self._build_canonical(
            self.ts, self.event_type, self.actor, self.session_id, self.repo, self.tier,
            self.payload, self.schema_version, self.host, self.pid, self.request_id,
        )

    @classmethod
    def create(
            cls,
            event_type: EventType,
            actor: str,
            session_id: SessionId,
            payload: dict[str, Any],
            repo: RepoSlug | None = None,
            tier: Tier | None = None,
            prev_hash: str = GENESIS,
            host: str | None = None,
            pid: int | None = None,
            request_id: str | None = None,
    ) -> AuditEvent:
        ts = iso_now()
        event_id = EventId.generate()
        canonical = cls._build_canonical(
            ts, event_type, actor, session_id, repo, tier, payload,
            ENVELOPE_SCHEMA_VERSION, host, pid, request_id,
        )
        event_hash = hashlib.sha256((prev_hash + canonical).encode("utf-8")).hexdigest()
        return cls(
            event_id=event_id,
            ts=ts,
            event_type=event_type,
            actor=actor,
            session_id=session_id,
            repo=repo,
            tier=tier,
            payload=payload,
            prev_hash=prev_hash,
            event_hash=event_hash,
            schema_version=ENVELOPE_SCHEMA_VERSION,
            host=host,
            pid=pid,
            request_id=request_id,
        )

    @staticmethod
    def _build_canonical(
            ts: str,
            event_type: EventType,
            actor: str,
            session_id: SessionId,
            repo: RepoSlug | None,
            tier: Tier | None,
            payload: dict[str, Any],
            schema_version: int = ENVELOPE_SCHEMA_VERSION,
            host: str | None = None,
            pid: int | None = None,
            request_id: str | None = None,
    ) -> str:
        return json.dumps({
            "schema_version": schema_version,
            "ts": ts,
            "event_type": event_type.value,
            "actor": actor,
            "session_id": str(session_id),
            "repo": str(repo) if repo else None,
            "tier": int(tier) if tier is not None else None,
            "host": host,
            "pid": pid,
            "request_id": request_id,
            "body": payload,
        }, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def verify(self, prev_hash: str) -> bool:
        """Verify this event's hash chain."""
        expected = hashlib.sha256(
            (prev_hash + self.canonical_payload()).encode("utf-8")
        ).hexdigest()
        return expected == self.event_hash and self.prev_hash == prev_hash


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


@dataclass(frozen=True, slots=True)
class ChainVerificationResult:
    """Result of audit chain verification."""
    ok: bool
    broken_at: EventId | None
    total_events: int


def verify_chain(events: list[AuditEvent]) -> ChainVerificationResult:
    """Verify a chain of fully-typed, in-memory events (e.g. freshly created)."""
    prev_hash = GENESIS
    for i, event in enumerate(events):
        if not event.verify(prev_hash):
            return ChainVerificationResult(False, event.event_id, i)
        prev_hash = event.event_hash
    return ChainVerificationResult(True, None, len(events))


def compute_event_hash(prev_hash: str, canonical_json: str) -> str:
    """The one place the chain's hash function is defined — everything that
    writes or verifies the ledger must call this, not reimplement it."""
    return hashlib.sha256((prev_hash + canonical_json).encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """A persisted ledger row, verified directly against its own stored
    canonical envelope. Deliberately avoids reconstructing a typed AuditEvent
    from separate columns: that reconstruction previously drifted from what
    was actually hashed at write time (dropped fields, regenerated
    session/request ids, mismatched payload shape), which silently made
    chain verification fail even for an untampered ledger. Storing and
    replaying the exact canonical string closes that gap for good.
    """
    event_id: EventId
    canonical_json: str
    prev_hash: str
    event_hash: str

    def verify(self, expected_prev_hash: str) -> bool:
        return (
            self.prev_hash == expected_prev_hash
            and compute_event_hash(self.prev_hash, self.canonical_json) == self.event_hash
        )


def verify_ledger_chain(rows: list[LedgerRow]) -> ChainVerificationResult:
    """Verify a persisted chain read back from storage (see LedgerRow)."""
    prev_hash = GENESIS
    for i, row in enumerate(rows):
        if not row.verify(prev_hash):
            return ChainVerificationResult(False, row.event_id, i)
        prev_hash = row.event_hash
    return ChainVerificationResult(True, None, len(rows))
