"""
Tests for claudenv domain audit.
"""
from __future__ import annotations

from claudenv.domain.audit import (
    AgentActionProjection,
    AuditEvent,
    EventType,
    HumanApprovalProjection,
    LedgerRow,
    PolicyViolationProjection,
    ProjectionBuilder,
    RetrievalProjection,
    SecurityProjection,
    ToolCallProjection,
    compute_event_hash,
    verify_chain,
    verify_ledger_chain,
)
from claudenv.domain.value_objects import (
    RepoSlug,
    SessionId,
    Tier,
    iso_now,
)


class TestEventType:
    def test_values(self):
        assert EventType.TOOL_CALL.value == "tool_call"
        assert EventType.AGENT_ACTION.value == "agent_action"
        assert EventType.RETRIEVAL.value == "retrieval"
        assert EventType.MEMORY_READ.value == "memory_read"
        assert EventType.MEMORY_WRITE.value == "memory_write"
        assert EventType.SECURITY_EVENT.value == "security_event"
        assert EventType.POLICY_VIOLATION.value == "policy_violation"
        assert EventType.HUMAN_APPROVAL_REQUEST.value == "human_approval_request"
        assert EventType.HUMAN_APPROVAL_RESOLVE.value == "human_approval_resolve"


class TestAuditEvent:
    def test_create(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="test-agent",
            session_id=SessionId.from_string("test-session"),
            payload={"tool": "read", "path": "test.py"},
        )
        assert event.event_type == EventType.TOOL_CALL
        assert event.actor == "test-agent"
        assert event.session_id == SessionId.from_string("test-session")
        assert event.payload == {"tool": "read", "path": "test.py"}
        assert event.prev_hash == "GENESIS"
        assert event.event_hash is not None

    def test_create_with_repo_tier(self):
        event = AuditEvent.create(
            event_type=EventType.POLICY_VIOLATION,
            actor="policy-engine",
            session_id=SessionId.from_string("test-session"),
            payload={"path": "secret.txt", "rule": "no-secrets"},
            repo=RepoSlug.from_string("my/repo"),
            tier=Tier.SENSITIVE,
        )
        assert event.repo == RepoSlug.from_string("my/repo")
        assert event.tier == Tier.SENSITIVE

    def test_canonical_payload(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="test-agent",
            session_id=SessionId.from_string("test-session"),
            payload={"tool": "read", "path": "test.py"},
        )
        canonical = event.canonical_payload()
        assert "ts" in canonical
        assert "event_type" in canonical
        assert "actor" in canonical
        assert "session_id" in canonical

    def test_verify_chain(self):
        event1 = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent1",
            session_id=SessionId.from_string("session1"),
            payload={"tool": "read"},
        )
        event2 = AuditEvent.create(
            event_type=EventType.AGENT_ACTION,
            actor="agent1",
            session_id=SessionId.from_string("session1"),
            payload={"action": "write"},
            prev_hash=event1.event_hash,
        )
        # Both should verify
        assert event1.verify("GENESIS")
        assert event2.verify(event1.event_hash)

    def test_verify_fails_on_tamper(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent1",
            session_id=SessionId.from_string("session1"),
            payload={"tool": "read"},
        )
        # Tamper with payload
        tampered = AuditEvent(
            event_id=event.event_id,
            ts=event.ts,
            event_type=event.event_type,
            actor=event.actor,
            session_id=event.session_id,
            repo=event.repo,
            tier=event.tier,
            payload={"tool": "write"},  # Changed!
            prev_hash=event.prev_hash,
            event_hash=event.event_hash,
        )
        assert not tampered.verify("GENESIS")


class TestProjections:
    def test_tool_call_projection(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={
                "tool": "filesystem.read",
                "args": {"path": "test.py"},
                "result_kind": "ok",
                "duration_ms": 100,
            },
        )
        proj = ProjectionBuilder.tool_call(event)
        assert isinstance(proj, ToolCallProjection)
        assert proj.tool == "filesystem.read"
        assert "test.py" in proj.args_json
        assert proj.result_kind == "ok"
        assert proj.duration_ms == 100

    def test_agent_action_projection(self):
        event = AuditEvent.create(
            event_type=EventType.AGENT_ACTION,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={
                "agent": "test-agent",
                "action": "analyze",
                "target": "code.py",
                "summary": "Analyzed code",
                "success": True,
            },
        )
        proj = ProjectionBuilder.agent_action(event)
        assert isinstance(proj, AgentActionProjection)
        assert proj.agent == "test-agent"
        assert proj.action == "analyze"
        assert proj.target == "code.py"
        assert proj.summary == "Analyzed code"
        assert proj.success is True

    def test_retrieval_projection(self):
        event = AuditEvent.create(
            event_type=EventType.RETRIEVAL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={
                "repo": "my/repo",
                "branch": "main",
                "query": "test query",
                "top_k": 10,
                "returned": 5,
                "reranked": True,
                "duration_ms": 50,
            },
        )
        proj = ProjectionBuilder.retrieval(event)
        assert isinstance(proj, RetrievalProjection)
        assert proj.repo == "my/repo"
        assert proj.query == "test query"
        assert proj.top_k == 10
        assert proj.returned == 5
        assert proj.reranked is True

    def test_memory_read_projection(self):
        event = AuditEvent.create(
            event_type=EventType.MEMORY_READ,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={
                "namespace": "proj-my-repo",
                "memory_type": "semantic",
                "query": "test query",
                "hit_count": 3,
            },
        )
        proj = ProjectionBuilder.memory_read(event)
        assert proj["namespace"] == "proj-my-repo"
        assert proj["memory_type"] == "semantic"
        assert proj["hit_count"] == 3

    def test_memory_write_projection(self):
        event = AuditEvent.create(
            event_type=EventType.MEMORY_WRITE,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={
                "namespace": "proj-my-repo",
                "memory_type": "episodic",
                "node_id": "node-123",
                "operation": "create",
            },
        )
        proj = ProjectionBuilder.memory_write(event)
        assert proj["namespace"] == "proj-my-repo"
        assert proj["memory_type"] == "episodic"
        assert proj["node_id"] == "node-123"
        assert proj["operation"] == "create"

    def test_security_event_projection(self):
        event = AuditEvent.create(
            event_type=EventType.SECURITY_EVENT,
            actor="detector",
            session_id=SessionId.from_string("session"),
            payload={
                "category": "secret_detection",
                "severity": "high",
                "detail": "AWS key found",
                "source": "filesystem.read",
            },
        )
        proj = ProjectionBuilder.security_event(event)
        assert isinstance(proj, SecurityProjection)
        assert proj.category == "secret_detection"
        assert proj.severity == "high"

    def test_policy_violation_projection(self):
        event = AuditEvent.create(
            event_type=EventType.POLICY_VIOLATION,
            actor="policy-engine",
            session_id=SessionId.from_string("session"),
            payload={
                "repo": "my/repo",
                "tier": 2,
                "path": "secret.txt",
                "rule": "no-secrets",
                "decision": "block",
            },
            repo=RepoSlug.from_string("my/repo"),
            tier=Tier.SENSITIVE,
        )
        proj = ProjectionBuilder.policy_violation(event)
        assert isinstance(proj, PolicyViolationProjection)
        assert proj.repo == "my/repo"
        assert proj.path == "secret.txt"
        assert proj.decision == "block"

    def test_human_approval_request_projection(self):
        event = AuditEvent.create(
            event_type=EventType.HUMAN_APPROVAL_REQUEST,
            actor="approval-gate",
            session_id=SessionId.from_string("session"),
            payload={
                "request_id": "appr-123",
                "agent": "test-agent",
                "repo": "my/repo",
                "tier": 2,
                "action": "write",
            },
        )
        proj = ProjectionBuilder.human_approval_request(event)
        assert isinstance(proj, HumanApprovalProjection)
        assert str(proj.request_id) == "appr-123"
        assert proj.agent == "test-agent"
        assert proj.decision == "pending"

    def test_human_approval_resolve_projection(self):
        event = AuditEvent.create(
            event_type=EventType.HUMAN_APPROVAL_RESOLVE,
            actor="approval-gate",
            session_id=SessionId.from_string("session"),
            payload={
                "request_id": "appr-123",
                "decision": "approved",
                "decided_by": "user@host",
                "decided_at": iso_now(),
            },
        )
        proj = ProjectionBuilder.human_approval_resolve(event)
        assert isinstance(proj, HumanApprovalProjection)
        assert proj.decision == "approved"
        assert proj.decided_by == "user@host"


class TestChainVerification:
    def test_verify_valid_chain(self):
        events = []
        prev_hash = "GENESIS"
        for i in range(3):
            event = AuditEvent.create(
                event_type=EventType.TOOL_CALL,
                actor="agent",
                session_id=SessionId.from_string("session"),
                payload={"tool": f"tool{i}"},
                prev_hash=prev_hash,
            )
            events.append(event)
            prev_hash = event.event_hash

        result = verify_chain(events)
        assert result.ok is True
        assert result.broken_at is None
        assert result.total_events == 3

    def test_verify_broken_chain(self):
        event1 = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        event2 = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool2"},
            prev_hash="WRONG_HASH",  # Wrong prev hash
        )

        result = verify_chain([event1, event2])
        assert result.ok is False
        assert result.broken_at == event2.event_id
        assert result.total_events == 1  # stops at first broken event

    def test_verify_empty_chain(self):
        result = verify_chain([])
        assert result.ok is True
        assert result.total_events == 0

    def test_verify_single_event(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        result = verify_chain([event])
        assert result.ok is True
        assert result.total_events == 1


class TestTraceMetadata:
    """schema_version/host/pid/request_id are governance trace fields folded
    into the hashed envelope — tampering with them must break verification
    exactly like tampering with the body does."""

    def test_create_populates_trace_fields(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
            host="build-box-1",
            pid=4242,
            request_id="req-abc123",
        )
        assert event.schema_version == 2
        assert event.host == "build-box-1"
        assert event.pid == 4242
        assert event.request_id == "req-abc123"
        assert event.verify("GENESIS")

    def test_trace_fields_default_to_none(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        assert event.host is None
        assert event.pid is None
        assert event.request_id is None
        assert event.verify("GENESIS")

    def test_tampered_host_breaks_verification(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
            host="real-host",
        )
        tampered = AuditEvent(
            event_id=event.event_id, ts=event.ts, event_type=event.event_type,
            actor=event.actor, session_id=event.session_id, repo=event.repo,
            tier=event.tier, payload=event.payload, prev_hash=event.prev_hash,
            event_hash=event.event_hash, schema_version=event.schema_version,
            host="attacker-host",  # Changed!
            pid=event.pid, request_id=event.request_id,
        )
        assert not tampered.verify("GENESIS")

    def test_tampered_request_id_breaks_verification(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
            request_id="req-original",
        )
        tampered = AuditEvent(
            event_id=event.event_id, ts=event.ts, event_type=event.event_type,
            actor=event.actor, session_id=event.session_id, repo=event.repo,
            tier=event.tier, payload=event.payload, prev_hash=event.prev_hash,
            event_hash=event.event_hash, schema_version=event.schema_version,
            host=event.host, pid=event.pid,
            request_id="req-forged",  # Changed!
        )
        assert not tampered.verify("GENESIS")


class TestLedgerRow:
    """LedgerRow/verify_ledger_chain verify a *persisted* chain directly
    against its stored canonical envelope, rather than reconstructing a
    typed AuditEvent from separate columns — the reconstruction approach
    previously drifted from what was actually hashed at write time and made
    verification fail even for an untampered ledger."""

    def _row_for(self, event: AuditEvent) -> LedgerRow:
        return LedgerRow(
            event_id=event.event_id,
            canonical_json=event.canonical_payload(),
            prev_hash=event.prev_hash,
            event_hash=event.event_hash,
        )

    def test_compute_event_hash_matches_create(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        assert compute_event_hash("GENESIS", event.canonical_payload()) == event.event_hash

    def test_ledger_row_verifies_untampered_row(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        assert self._row_for(event).verify("GENESIS")

    def test_ledger_row_rejects_wrong_prev_hash(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        assert not self._row_for(event).verify("SOME_OTHER_HASH")

    def test_ledger_row_rejects_tampered_canonical_json(self):
        event = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        row = self._row_for(event)
        tampered = LedgerRow(
            event_id=row.event_id,
            canonical_json=row.canonical_json.replace("tool1", "tool2"),
            prev_hash=row.prev_hash,
            event_hash=row.event_hash,
        )
        assert not tampered.verify("GENESIS")

    def test_verify_ledger_chain_valid(self):
        rows = []
        prev_hash = "GENESIS"
        for i in range(3):
            event = AuditEvent.create(
                event_type=EventType.TOOL_CALL,
                actor="agent",
                session_id=SessionId.from_string("session"),
                payload={"tool": f"tool{i}"},
                prev_hash=prev_hash,
            )
            rows.append(self._row_for(event))
            prev_hash = event.event_hash

        result = verify_ledger_chain(rows)
        assert result.ok is True
        assert result.broken_at is None
        assert result.total_events == 3

    def test_verify_ledger_chain_detects_break(self):
        event1 = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool1"},
        )
        event2 = AuditEvent.create(
            event_type=EventType.TOOL_CALL,
            actor="agent",
            session_id=SessionId.from_string("session"),
            payload={"tool": "tool2"},
            prev_hash="WRONG_HASH",
        )
        result = verify_ledger_chain([self._row_for(event1), self._row_for(event2)])
        assert result.ok is False
        assert result.broken_at == event2.event_id
        assert result.total_events == 1

    def test_verify_ledger_chain_empty(self):
        result = verify_ledger_chain([])
        assert result.ok is True
        assert result.total_events == 0
