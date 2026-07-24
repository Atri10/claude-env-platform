"""claude-env :: Domain - Memory - Graph"""
from __future__ import annotations

from typing import Any

from claudenv.domain.memory import MemoryNode, MemoryType, NamespaceConfig
from claudenv.domain.memory.service.decay import MemoryDecay
from claudenv.domain.memory.service.interfaces import (
    IEmbeddingProvider,
    IMemoryGraph,
    IMemoryRepository,
)
from claudenv.domain.memory.service.reader import MemoryReader
from claudenv.domain.memory.service.traversal import MemoryGraphTraversal
from claudenv.domain.memory.service.writer import MemoryWriter
from claudenv.domain.value_objects import NodeId, Tier


class MemoryGraph(IMemoryGraph):
    """Composite memory graph combining all focused services.

    This class maintains backward compatibility with the IMemoryGraph port
    while delegating to focused services internally.
    """

    def __init__(
            self,
            repo: IMemoryRepository,
            namespace: str,
            embedding: IEmbeddingProvider | None = None,
            isolated: bool = False,
            shared_namespaces: tuple[str, ...] = (),
            tier: Tier = Tier.INTERNAL,
    ):
        self._writer = MemoryWriter(repo, namespace, embedding)
        self._reader = MemoryReader(repo, namespace, isolated, shared_namespaces, tier)
        self._decay = MemoryDecay(repo)
        self._traversal = MemoryGraphTraversal(repo, namespace, embedding, isolated, shared_namespaces)

    # --- IMemoryWriter ---
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
        return self._writer.add_node(memory_type, node_kind, name, body, repo, confidence, embedding)

    def supersede(
            self,
            old_node_id: NodeId,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            **kwargs,
    ) -> NodeId:
        return self._writer.supersede(old_node_id, memory_type, node_kind, name, body, **kwargs)

    def add_edge(
            self,
            src: NodeId,
            dst: NodeId,
            relation: str,
            weight: float = 1.0,
    ) -> str:
        return self._writer.add_edge(src, dst, relation, weight)

    # --- IMemoryReader ---
    def get_node(self, node_id: NodeId) -> MemoryNode | None:
        return self._reader.get_node(node_id)

    def list_nodes(
            self,
            memory_type: MemoryType | None = None,
            min_confidence: float = 0.0,
            limit: int = 100,
    ) -> list[MemoryNode]:
        return self._reader.list_nodes(
            namespace=self._reader.namespace,
            memory_type=memory_type,
            min_confidence=min_confidence,
            include_superseded=False,
            limit=limit,
        )

    def get_namespace_config(self) -> NamespaceConfig:
        return self._reader.get_namespace_config()

    # --- IMemoryDecay ---
    def decay(self) -> int:
        return self._decay.decay_all(self._reader.namespace)

    def decay_all(self, namespace: str) -> int:
        return self._decay.decay_all(namespace)

    # --- IMemoryGraphTraversal ---
    def recall(
            self,
            query: str,
            depth: int = 2,
            top_k: int = 10,
            query_vector: list[float] | None = None,
            extra_namespaces: list[str] | None = None,
            memory_type: MemoryType | None = None,
    ) -> list[MemoryNode]:
        return self._traversal.recall(query, depth, top_k, query_vector, extra_namespaces, memory_type)

    def expand(
            self,
            seed_ids: list[NodeId],
            depth: int = 2,
            relations: list[str] | None = None,
            extra_namespaces: list[str] | None = None,
    ) -> list[MemoryNode]:
        return self._traversal.expand(seed_ids, depth, relations, extra_namespaces)
