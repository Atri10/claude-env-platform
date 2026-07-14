"""
claude-env :: Domain - Audit Entities - Events

Groups: EventType, AuditEvent, LedgerRow.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any

from claudenv.domain.audit.chain import ENVELOPE_SCHEMA_VERSION, GENESIS, compute_event_hash
from claudenv.domain.value_objects import (
    EventId,
    RepoSlug,
    SessionId,
    Tier,
    iso_now,
)


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
        event_hash = compute_event_hash(prev_hash, canonical)
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
        expected = compute_event_hash(prev_hash, self.canonical_payload())
        return expected == self.event_hash and self.prev_hash == prev_hash


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
