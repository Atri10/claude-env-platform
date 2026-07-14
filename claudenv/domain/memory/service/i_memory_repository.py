"""
claude-env :: Domain - Memory Service (Domain Layer) - IMemoryRepository
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.memory import MemoryEdge, MemoryNode, MemoryType
from claudenv.domain.value_objects import NodeId


class IMemoryRepository(Protocol):
    """Memory graph persistence port."""

    @abstractmethod
    def insert_node(self, node: MemoryNode) -> None:
        ...

    @abstractmethod
    def update_node(self, node: MemoryNode) -> None:
        ...

    @abstractmethod
    def get_node(self, node_id: NodeId) -> MemoryNode | None:
        ...

    @abstractmethod
    def list_nodes(
            self,
            namespace: str,
            memory_type: MemoryType | None = None,
            min_confidence: float = 0.0,
            include_superseded: bool = False,
            limit: int = 100,
    ) -> list[MemoryNode]:
        ...

    @abstractmethod
    def decay_all(self, namespace: str) -> int:
        ...

    @abstractmethod
    def insert_edge(self, edge: MemoryEdge) -> None:
        ...

    @abstractmethod
    def get_edges(
            self,
            src: NodeId,
            dst: NodeId | None = None,
            relation: str | None = None,
    ) -> list[MemoryEdge]:
        ...

    @abstractmethod
    def expand_graph(
            self,
            seed_ids: list[NodeId],
            depth: int,
            relations: list[str] | None,
            namespace: str,
            extra_namespaces: list[str] | None,
    ) -> list[MemoryNode]:
        ...

    @abstractmethod
    def delete_node(self, node_id: NodeId) -> None:
        ...
