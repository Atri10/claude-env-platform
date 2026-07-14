"""
claude-env :: Domain - Memory Entities - MemoryNode
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from claudenv.domain.value_objects import NodeId, utc_now
from claudenv.domain.memory.memory_type import MemoryType
from claudenv.domain.memory.node_kind import NodeKind
from claudenv.domain.memory._kinds import HALF_LIFE_DAYS


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
