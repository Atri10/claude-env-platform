"""
Tests for claudenv domain audit.
"""
from __future__ import annotations

import json
import pytest

from claudenv.domain.audit import (
    AuditEvent, EventType, ToolCallProjection, AgentActionProjection,
    RetrievalProjection, SecurityProjection, PolicyViolationProjection,
    HumanApprovalProjection, ProjectionBuilder, verify_chain,
)
from claudenv.domain.value_objects import (
    RepoSlug, SessionId, Tier, iso_now,
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
