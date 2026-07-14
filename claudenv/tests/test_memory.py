"""
Tests for claudenv application memory layer - minimal working tests.
"""
from __future__ import annotations

import pytest

from claudenv.domain.memory import (
    HALF_LIFE_DAYS,
    VALID_KINDS,
    EdgeRelation,
    InvalidMemoryKind,
    MemoryEdge,
    MemoryNode,
    MemoryType,
    Namespace,
    NamespaceConfig,
    NodeKind,
    effective_confidence,
    validate_memory_kind,
)
from claudenv.domain.value_objects import (
    NodeId,
    RepoSlug,
    Tier,
    utc_now,
)


class TestMemoryType:
    def test_values(self):
        assert MemoryType.EPISODIC.value == "episodic"
        assert MemoryType.SEMANTIC.value == "semantic"
        assert MemoryType.PROCEDURAL.value == "procedural"
        assert MemoryType.AGENT.value == "agent"


class TestNodeKind:
    def test_values(self):
        assert NodeKind.SESSION.value == "session"
        assert NodeKind.DECISION.value == "decision"
        assert NodeKind.ENTITY.value == "entity"
        assert NodeKind.CONCEPT.value == "concept"
        assert NodeKind.WORKFLOW.value == "workflow"


class TestEdgeRelation:
    def test_values(self):
        assert EdgeRelation.RELATES_TO.value == "RELATES_TO"
        assert EdgeRelation.DEPENDS_ON.value == "DEPENDS_ON"
        assert EdgeRelation.SUPERSEDES.value == "SUPERSEDES"


class TestValidKinds:
    def test_episodic_valid(self):
        assert NodeKind.SESSION in VALID_KINDS[MemoryType.EPISODIC]
        assert NodeKind.DECISION in VALID_KINDS[MemoryType.EPISODIC]
        assert NodeKind.INVESTIGATION in VALID_KINDS[MemoryType.EPISODIC]

    def test_semantic_valid(self):
        assert NodeKind.ENTITY in VALID_KINDS[MemoryType.SEMANTIC]
        assert NodeKind.CONCEPT in VALID_KINDS[MemoryType.SEMANTIC]
        assert NodeKind.ARCHITECTURE in VALID_KINDS[MemoryType.SEMANTIC]

    def test_procedural_valid(self):
        assert NodeKind.WORKFLOW in VALID_KINDS[MemoryType.PROCEDURAL]
        assert NodeKind.CONVENTION in VALID_KINDS[MemoryType.PROCEDURAL]

    def test_agent_valid_all(self):
        # Agent namespace can use any kind
        for kind in NodeKind:
            assert kind in VALID_KINDS[MemoryType.AGENT]


class TestHalfLifeDays:
    def test_values_exist(self):
        assert HALF_LIFE_DAYS[NodeKind.SESSION] == 30
        assert HALF_LIFE_DAYS[NodeKind.DECISION] == 365
        assert HALF_LIFE_DAYS[NodeKind.WORKFLOW] == 540


class TestMemoryNode:
    def test_create(self):
        node = MemoryNode.create(
            namespace="test-ns",
            memory_type=MemoryType.SEMANTIC,
            node_kind=NodeKind.ENTITY,
            name="TestEntity",
            body={"description": "A test entity"},
        )
        assert node.namespace == "test-ns"
        assert node.memory_type == MemoryType.SEMANTIC
        assert node.node_kind == NodeKind.ENTITY
        assert node.name == "TestEntity"
        assert node.body == {"description": "A test entity"}
        assert node.confidence == 1.0
        assert node.half_life_days == 180
        assert node.access_count == 0
        assert not node.is_superseded()

    def test_create_with_repo(self):
        node = MemoryNode.create(
            namespace="test-ns",
            memory_type=MemoryType.SEMANTIC,
            node_kind=NodeKind.ENTITY,
            name="TestEntity",
            body={"type": "class"},
            repo="my/repo",
        )
        assert node.repo == "my/repo"

    def test_create_with_embedding(self):
        embedding = b"\x00\x01\x02\x03" * 4
        node = MemoryNode.create(
            namespace="test-ns",
            memory_type=MemoryType.SEMANTIC,
            node_kind=NodeKind.CONCEPT,
            name="TestConcept",
            body={},
            embedding=embedding,
        )
        assert node.embedding == embedding

    def test_effective_confidence_fresh(self):
        node = MemoryNode.create(
            namespace="test-ns",
            memory_type=MemoryType.EPISODIC,
            node_kind=NodeKind.SESSION,
            name="test",
            body={},
            confidence=1.0,
        )
        # Fresh node should have near-full confidence
        assert node.effective_confidence > 0.99

    def test_effective_confidence_decay(self):
        import datetime
        old_time = utc_now() - datetime.timedelta(days=60)

        node = MemoryNode(
            node_id=NodeId.generate(),
            namespace="test-ns",
            memory_type=MemoryType.EPISODIC,
            node_kind=NodeKind.SESSION,
            name="test",
            body={},
            repo=None,
            confidence=1.0,
            half_life_days=30,
            created_at=old_time,
            updated_at=old_time,
            last_access=old_time,
            access_count=0,
        )
        # After 60 days (2 half-lives), confidence should be ~0.25
        assert abs(node.effective_confidence - 0.25) < 0.01

    def test_is_superseded(self):
        node = MemoryNode.create(
            namespace="test-ns",
            memory_type=MemoryType.EPISODIC,
            node_kind=NodeKind.SESSION,
            name="test",
            body={},
        )
        assert not node.is_superseded()

        from dataclasses import replace
        superseded = replace(node, superseded_by="other-id")
        assert superseded.is_superseded()

    def test_to_dict(self):
        node = MemoryNode.create(
            namespace="test-ns",
            memory_type=MemoryType.EPISODIC,
            node_kind=NodeKind.SESSION,
            name="test",
            body={"key": "value"},
        )
        d = node.to_dict()
        assert d["namespace"] == "test-ns"
        assert d["memory_type"] == "episodic"
        assert d["node_kind"] == "session"
        assert d["name"] == "test"
        assert d["body_json"] == '{"key": "value"}'

    def test_from_dict(self):
        node = MemoryNode.create(
            namespace="test-ns",
            memory_type=MemoryType.EPISODIC,
            node_kind=NodeKind.SESSION,
            name="test",
            body={"key": "value"},
        )
        d = node.to_dict()
        restored = MemoryNode.from_dict(d)
        assert restored.node_id == node.node_id
        assert restored.name == node.name
        assert restored.body == node.body


class TestMemoryEdge:
    def test_create(self):
        src = NodeId.generate()
        dst = NodeId.generate()
        edge = MemoryEdge.create(
            namespace="test-ns",
            src=src,
            dst=dst,
            relation=EdgeRelation.RELATES_TO,
            weight=0.8,
        )
        assert edge.namespace == "test-ns"
        assert edge.src == src
        assert edge.dst == dst
        assert edge.relation == EdgeRelation.RELATES_TO
        assert edge.weight == 0.8

    def test_to_dict(self):
        src = NodeId.generate()
        dst = NodeId.generate()
        edge = MemoryEdge.create(
            namespace="test-ns",
            src=src,
            dst=dst,
            relation=EdgeRelation.DEPENDS_ON,
        )
        d = edge.to_dict()
        assert d["namespace"] == "test-ns"
        assert d["src"] == str(src)
        assert d["dst"] == str(dst)
        assert d["rel"] == "DEPENDS_ON"

    def test_from_dict(self):
        src = NodeId.generate()
        dst = NodeId.generate()
        edge = MemoryEdge.create(
            namespace="test-ns",
            src=src,
            dst=dst,
            relation=EdgeRelation.RELATES_TO,
        )
        d = edge.to_dict()
        restored = MemoryEdge.from_dict(d)
        assert restored.edge_id == edge.edge_id
        assert restored.src == edge.src
        assert restored.dst == edge.dst


class TestNamespace:
    def test_project(self):
        slug = RepoSlug.generate("my/repo")
        ns = Namespace.project(slug)
        assert ns.name == "proj-my-repo"
        assert ns.isolated is False

    def test_agent(self):
        ns = Namespace.agent("agent-123")
        assert ns.name == "agent:agent-123"
        assert ns.isolated is True

    def test_global(self):
        ns = Namespace.global_ns()
        assert ns.name == "global"
        assert ns.isolated is False

    def test_can_read_same(self):
        ns = Namespace(name="test", isolated=False)
        assert ns.can_read(ns)

    def test_can_read_isolated(self):
        ns1 = Namespace(name="test", isolated=True)
        ns2 = Namespace(name="test", isolated=True)
        assert ns1.can_read(ns2)  # same name

        ns3 = Namespace(name="other", isolated=True)
        assert not ns1.can_read(ns3)  # different name

    def test_can_read_shared(self):
        ns1 = Namespace(name="test", isolated=True, shared_with=("other",))
        ns2 = Namespace(name="other", isolated=True)
        assert ns1.can_read(ns2)

    def test_can_read_global(self):
        ns1 = Namespace.global_ns()
        ns2 = Namespace(name="anything", isolated=False)
        assert ns1.can_read(ns2)
        assert ns2.can_read(ns1)


class TestNamespaceConfig:
    def test_create(self):
        config = NamespaceConfig(
            name="test-ns",
            isolated=True,
            tier=Tier.SENSITIVE,
        )
        assert config.name == "test-ns"
        assert config.isolated is True
        assert config.tier == Tier.SENSITIVE

    def test_defaults(self):
        config = NamespaceConfig(name="test")
        assert config.isolated is False
        assert config.tier == Tier.INTERNAL
        assert config.shared_with == ()


class TestEffectiveConfidence:
    def test_no_decay(self):
        now = utc_now()
        conf = effective_confidence(1.0, 30, now)
        assert abs(conf - 1.0) < 0.001

    def test_one_half_life(self):
        import datetime
        past = utc_now() - datetime.timedelta(days=30)
        conf = effective_confidence(1.0, 30, past)
        assert abs(conf - 0.5) < 0.01

    def test_two_half_lives(self):
        import datetime
        past = utc_now() - datetime.timedelta(days=60)
        conf = effective_confidence(1.0, 30, past)
        assert abs(conf - 0.25) < 0.01

    def test_fast_decay(self):
        import datetime
        # Half-life of 1 day, 5 days ago
        past = utc_now() - datetime.timedelta(days=5)
        conf = effective_confidence(1.0, 1, past)
        assert conf < 0.1


class TestValidateMemoryKind:
    def test_valid(self):
        validate_memory_kind(MemoryType.EPISODIC, NodeKind.SESSION)
        validate_memory_kind(MemoryType.SEMANTIC, NodeKind.ENTITY)
        validate_memory_kind(MemoryType.PROCEDURAL, NodeKind.WORKFLOW)
        validate_memory_kind(MemoryType.AGENT, NodeKind.CONCEPT)  # agent accepts all

    def test_invalid(self):
        with pytest.raises(InvalidMemoryKind):
            validate_memory_kind(MemoryType.EPISODIC, NodeKind.ENTITY)

        with pytest.raises(InvalidMemoryKind):
            validate_memory_kind(MemoryType.SEMANTIC, NodeKind.SESSION)
