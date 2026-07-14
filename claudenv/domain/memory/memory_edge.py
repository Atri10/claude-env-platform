"""
claude-env :: Domain - Memory Entities - MemoryEdge
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from claudenv.domain.value_objects import EdgeId, NodeId, utc_now
from claudenv.domain.memory.edge_relation import EdgeRelation


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
