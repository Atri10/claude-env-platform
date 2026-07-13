"""
claude-env :: Ports - Memory Interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.memory import MemoryEdge, MemoryNode, NamespaceConfig
from claudenv.domain.value_objects import (
    MemoryType, NodeId, )


class IMemoryRepository(Protocol):
    """Memory graph persistence."""

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


class IMemoryGraph(Protocol):
    """High-level memory graph operations."""

    @abstractmethod
    def add_node(
            self,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            repo: str | None = None,
            confidence: float = 1.0,
            embedding: bytes | None = None,
    ) -> NodeId:
        ...

    @abstractmethod
    def get_node(self, node_id: NodeId) -> MemoryNode | None:
        ...

    @abstractmethod
    def supersede(
            self,
            old_node_id: NodeId,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            **kwargs,
    ) -> NodeId:
        ...

    @abstractmethod
    def add_edge(
            self,
            src: NodeId,
            dst: NodeId,
            relation: str,
            weight: float = 1.0,
    ) -> str:
        ...

    @abstractmethod
    def recall(
            self,
            query: str,
            depth: int = 2,
            top_k: int = 10,
            query_vector: list[float] | None = None,
            extra_namespaces: list[str] | None = None,
    ) -> list[MemoryNode]:
        ...

    @abstractmethod
    def expand(
            self,
            seed_ids: list[NodeId],
            depth: int = 2,
            relations: list[str] | None = None,
            extra_namespaces: list[str] | None = None,
    ) -> list[MemoryNode]:
        ...

    @abstractmethod
    def list_nodes(
            self,
            memory_type: MemoryType | None = None,
            min_confidence: float = 0.0,
            limit: int = 100,
    ) -> list[MemoryNode]:
        ...

    @abstractmethod
    def decay(self) -> int:
        ...

    @abstractmethod
    def get_namespace_config(self) -> NamespaceConfig:
        ...


class IEmbeddingProvider(Protocol):
    """Embedding model provider."""

    @abstractmethod
    def embed_text(self, text: str) -> bytes:
        ...

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[bytes]:
        ...

    @abstractmethod
    def dim(self) -> int:
        ...
