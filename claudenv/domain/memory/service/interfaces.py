"""
claude-env :: Domain - Memory Service (Domain Layer) - Interfaces

Groups all the I* protocols: IMemoryRepository, IMemoryWriter, IMemoryReader,
IMemoryDecay, IMemoryGraphTraversal, IMemoryGraph, IEmbeddingProvider,
IConfigProvider, IMemoryService.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.memory import MemoryEdge, MemoryNode, MemoryType, NamespaceConfig
from claudenv.domain.value_objects import NodeId, RepoSlug, Tier


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


class IMemoryWriter(Protocol):
    """Write operations for memory nodes."""

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


class IMemoryReader(Protocol):
    """Read operations for memory nodes."""

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
    def get_namespace_config(self) -> NamespaceConfig:
        ...


class IMemoryDecay(Protocol):
    """Memory decay operations."""

    @abstractmethod
    def decay_all(self, namespace: str) -> int:
        ...


class IMemoryGraphTraversal(Protocol):
    """Graph traversal and recall operations."""

    @abstractmethod
    def recall(
            self,
            query: str,
            depth: int = 2,
            top_k: int = 10,
            query_vector: list[float] | None = None,
            extra_namespaces: list[str] | None = None,
            memory_type: MemoryType | None = None,
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


class IMemoryGraph(IMemoryWriter, IMemoryReader, IMemoryGraphTraversal, Protocol):
    """High-level memory graph operations (composite of focused ports)."""

    @abstractmethod
    def decay(self) -> int:
        ...

    # All other methods inherited from focused ports


class IEmbeddingProvider(Protocol):
    """Embedding model provider."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        ...

    @abstractmethod
    def dim(self) -> int:
        ...



class IMemoryService(Protocol):
    """Port for memory service operations (consumer-owned interface).

    This is the interface that application-layer code depends on.
    The domain layer provides the implementation (MemoryServiceImpl).
    """

    @abstractmethod
    def create_graph(
            self,
            namespace: str,
            isolated: bool = False,
            tier: Tier = Tier.INTERNAL,
            shared_with: tuple[str, ...] = (),
    ) -> IMemoryGraph:
        """Create a memory graph for the given namespace."""
        ...

    @abstractmethod
    def get_project_graph(self, repo_slug: RepoSlug, tier: Tier) -> IMemoryGraph:
        """Get a project-scoped memory graph."""
        ...

    @abstractmethod
    def get_agent_graph(self, agent_id: str) -> IMemoryGraph:
        """Get an agent-scoped memory graph."""
        ...

    @abstractmethod
    def consolidate(self, namespace: str, dry_run: bool = False) -> dict[str, Any]:
        """Run memory consolidation."""
        ...

    @abstractmethod
    def prune(self, namespace: str, dry_run: bool = True) -> dict[str, Any]:
        """Run memory pruning."""
        ...

    @abstractmethod
    def sync_export(self, namespace: str) -> list[dict[str, Any]]:
        """Export memory namespace."""
        ...

    @abstractmethod
    def sync_import(self, namespace: str, data: list[dict[str, Any]]) -> int:
        """Import memory namespace."""
        ...
