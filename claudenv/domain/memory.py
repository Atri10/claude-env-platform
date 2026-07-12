"""
claude-env :: Domain - Memory Entities
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from claudenv.domain.value_objects import (
    BranchName, ContentHash, EdgeId, NodeId, RepoSlug, Tier, utc_now,
)


def effective_confidence(stored: float, half_life: float, updated_at: str | datetime) -> float:
    """Calculate time-decayed confidence."""
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age_days = max(0.0, (utc_now() - updated_at).total_seconds() / 86400)
    return stored * (2 ** (-age_days / max(1e-6, half_life)))


class MemoryType(str, Enum):
    """Top-level memory categories."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    AGENT = "agent"


class NodeKind(str, Enum):
    """Specific node kinds per memory type."""
    # Episodic
    SESSION = "session"
    DECISION = "decision"
    INVESTIGATION = "investigation"
    # Semantic
    ENTITY = "entity"
    CONCEPT = "concept"
    ARCHITECTURE = "architecture"
    PREFERENCE = "preference"
    # Procedural
    WORKFLOW = "workflow"
    CONVENTION = "convention"
    PATTERN = "pattern"


class EdgeRelation(str, Enum):
    """Edge relation types."""
    RELATES_TO = "RELATES_TO"
    DEPENDS_ON = "DEPENDS_ON"
    DECISION_ABOUT = "DECISION_ABOUT"
    DISCOVERED_IN = "DISCOVERED_IN"
    SUPERSEDES = "SUPERSEDES"
    CONSOLIDATES = "CONSOLIDATES"


# Valid (memory_type, node_kind) combinations
VALID_KINDS: dict[MemoryType, set[NodeKind]] = {
    MemoryType.EPISODIC: {NodeKind.SESSION, NodeKind.DECISION, NodeKind.INVESTIGATION},
    MemoryType.SEMANTIC: {NodeKind.ENTITY, NodeKind.CONCEPT, NodeKind.ARCHITECTURE, NodeKind.PREFERENCE},
    MemoryType.PROCEDURAL: {NodeKind.WORKFLOW, NodeKind.CONVENTION, NodeKind.PATTERN},
    MemoryType.AGENT: {  # Agent namespace reuses any kind
        NodeKind.SESSION, NodeKind.DECISION, NodeKind.INVESTIGATION,
        NodeKind.ENTITY, NodeKind.CONCEPT, NodeKind.ARCHITECTURE, NodeKind.PREFERENCE,
        NodeKind.WORKFLOW, NodeKind.CONVENTION, NodeKind.PATTERN,
    },
}

# Half-life in days per node kind
HALF_LIFE_DAYS: dict[NodeKind, int] = {
    NodeKind.SESSION: 30,
    NodeKind.INVESTIGATION: 30,
    NodeKind.DECISION: 365,
    NodeKind.ENTITY: 180,
    NodeKind.CONCEPT: 270,
    NodeKind.ARCHITECTURE: 365,
    NodeKind.PREFERENCE: 270,
    NodeKind.WORKFLOW: 540,
    NodeKind.CONVENTION: 540,
    NodeKind.PATTERN: 365,
}


@dataclass(frozen=True, slots=True)
class MemoryNode:
    """A node in the memory graph."""
    node_id: NodeId
    namespace: str
    memory_type: MemoryType
    node_kind: NodeKind
    name: str
    body: dict[str, Any]
    repo: str | None
    confidence: float  # 0.0 - 1.0
    half_life_days: int
    created_at: datetime
    updated_at: datetime
    last_access: datetime
    access_count: int
    embedding: bytes | None = None
    superseded_by: str | None = None

    @property
    def effective_confidence(self) -> float:
        """Time-decayed confidence."""
        age_days = (utc_now() - self.updated_at).total_seconds() / 86400
        if age_days <= 0:
            return self.confidence
        return self.confidence * (2 ** (-age_days / max(1e-6, self.half_life_days)))

    def is_superseded(self) -> bool:
        return self.superseded_by is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_id": str(self.node_id),
            "namespace": self.namespace,
            "memory_type": self.memory_type.value,
            "node_kind": self.node_kind.value,
            "name": self.name,
            "body_json": json.dumps(self.body),
            "repo": self.repo,
            "confidence": self.confidence,
            "half_life_days": self.half_life_days,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "last_access": self.last_access.isoformat(),
            "access_count": self.access_count,
            "embedding": self.embedding,
            "superseded_by": self.superseded_by,
        }

    @classmethod
    def create(
        cls,
        namespace: str,
        memory_type: MemoryType,
        node_kind: NodeKind,
        name: str,
        body: dict[str, Any],
        repo: str | None = None,
        confidence: float = 1.0,
        embedding: bytes | None = None,
    ) -> MemoryNode:
        now = utc_now()
        half_life = HALF_LIFE_DAYS.get(node_kind, 90)
        return cls(
            node_id=NodeId.generate(),
            namespace=namespace,
            memory_type=memory_type,
            node_kind=node_kind,
            name=name,
            body=body,
            repo=repo,
            confidence=confidence,
            half_life_days=half_life,
            created_at=now,
            updated_at=now,
            last_access=now,
            access_count=0,
            embedding=embedding,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryNode:
        return cls(
            node_id=NodeId.from_string(data["node_id"]),
            namespace=data["namespace"],
            memory_type=MemoryType(data["memory_type"]),
            node_kind=NodeKind(data["node_kind"]),
            name=data["name"],
            body=json.loads(data["body_json"]),
            repo=data.get("repo"),
            confidence=data["confidence"],
            half_life_days=data["half_life_days"],
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data["updated_at"].replace("Z", "+00:00")),
            last_access=datetime.fromisoformat(data["last_access"].replace("Z", "+00:00")),
            access_count=data["access_count"],
            embedding=data.get("embedding"),
            superseded_by=data.get("superseded_by"),
        )


@dataclass(frozen=True, slots=True)
class MemoryEdge:
    """A typed edge between memory nodes."""
    edge_id: EdgeId
    namespace: str
    src: NodeId
    dst: NodeId
    relation: EdgeRelation
    weight: float
    created_at: datetime

    def to_dict(self) -> dict[str, Any]:
        return {
            "edge_id": str(self.edge_id),
            "namespace": self.namespace,
            "src": str(self.src),
            "dst": str(self.dst),
            "rel": self.relation.value,
            "weight": self.weight,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def create(
        cls,
        namespace: str,
        src: NodeId,
        dst: NodeId,
        relation: EdgeRelation,
        weight: float = 1.0,
    ) -> MemoryEdge:
        return cls(
            edge_id=EdgeId.generate(),
            namespace=namespace,
            src=src,
            dst=dst,
            relation=relation,
            weight=weight,
            created_at=utc_now(),
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MemoryEdge:
        return cls(
            edge_id=EdgeId.from_string(data["edge_id"]),
            namespace=data["namespace"],
            src=NodeId.from_string(data["src"]),
            dst=NodeId.from_string(data["dst"]),
            relation=EdgeRelation(data["rel"]),
            weight=data["weight"],
            created_at=datetime.fromisoformat(data["created_at"].replace("Z", "+00:00")),
        )


@dataclass(frozen=True, slots=True)
class Namespace:
    """Memory namespace with isolation settings."""
    name: str
    isolated: bool = False
    shared_with: tuple[str, ...] = ()

    @classmethod
    def project(cls, slug: RepoSlug) -> Namespace:
        return cls(f"proj-{slug}", isolated=False)

    @classmethod
    def agent(cls, agent_id: str) -> Namespace:
        return cls(f"agent:{agent_id}", isolated=True)

    @classmethod
    def global_ns(cls) -> Namespace:
        return cls("global", isolated=False)

    def can_read(self, other: Namespace) -> bool:
        if self.isolated or other.isolated:
            return self.name == other.name or other.name in self.shared_with
        return True


def validate_memory_kind(memory_type: MemoryType, node_kind: NodeKind) -> None:
    """Validate that (memory_type, node_kind) is a valid combination."""
    allowed = VALID_KINDS.get(memory_type)
    if allowed is None or node_kind not in allowed:
        raise InvalidMemoryKind(
            f"Invalid combination: memory_type={memory_type.value}, "
            f"node_kind={node_kind.value}. "
            f"Valid kinds for {memory_type.value}: {', '.join(k.value for k in allowed)}"
        )


class InvalidMemoryKind(ValueError):
    """Raised when (memory_type, node_kind) isn't in the documented taxonomy."""


class InvalidMemoryRelation(ValueError):
    """Raised when an edge relation isn't in the documented vocabulary."""


class CrossNamespaceEdge(ValueError):
    """Raised when an edge would join nodes across namespaces (isolation leak)."""



@dataclass(frozen=True, slots=True)
class NamespaceConfig:
    """Configuration for a memory namespace."""
    name: str
    isolated: bool = False
    tier: Tier = Tier.INTERNAL
    shared_with: tuple[str, ...] = ()