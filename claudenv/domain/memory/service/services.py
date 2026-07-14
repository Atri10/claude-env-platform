"""
claude-env :: Domain - Memory Service (Domain Layer) - Services

Groups: MemoryWriter, MemoryReader, MemoryDecay, MemoryGraphTraversal,
MemoryGraph.
"""
from __future__ import annotations

import json
import struct
from dataclasses import replace
from typing import Any

from claudenv.domain.memory import (
    EdgeRelation, MemoryEdge, MemoryNode,
    MemoryType, NamespaceConfig, NodeKind,
    CrossNamespaceEdge, InvalidMemoryRelation,
    validate_memory_kind,
)
from claudenv.domain.value_objects import NodeId, Tier, utc_now
from claudenv.domain.memory.service.interfaces import (
    IMemoryWriter,
    IMemoryReader,
    IMemoryDecay,
    IMemoryGraphTraversal,
    IMemoryGraph,
    IMemoryRepository,
    IEmbeddingProvider,
)


class MemoryWriter(IMemoryWriter):
    """Write operations for memory nodes."""

    def __init__(
            self,
            repo: IMemoryRepository,
            namespace: str,
            embedding: IEmbeddingProvider | None = None,
    ):
        self.repo = repo
        self.namespace = namespace
        self.embedding = embedding

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
        node_kind_enum = NodeKind(node_kind)
        validate_memory_kind(memory_type, node_kind_enum)

        # Generate embedding if not provided. The embedding provider exposes
        # embed_documents()/embed_query() returning list[float] -- there is no
        # embed_text() method -- so we embed via embed_documents() and pack the
        # vector to bytes for storage/struct.unpack in recall().
        if embedding is None and self.embedding:
            text = name + " " + json.dumps(body)
            try:
                vec = self.embedding.embed_documents([text])[0]
                embedding = struct.pack(f"<{len(vec)}f", *vec)
            except Exception:
                pass

        node = MemoryNode.create(
            namespace=self.namespace,
            memory_type=memory_type,
            node_kind=node_kind_enum,
            name=name,
            body=body,
            repo=repo,
            confidence=confidence,
            embedding=embedding,
        )
        self.repo.insert_node(node)
        return node.node_id

    def supersede(
            self,
            old_node_id: NodeId,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            **kwargs,
    ) -> NodeId:
        node_kind_enum = NodeKind(node_kind)
        new_node = self.add_node(memory_type, node_kind_enum.value, name, body, **kwargs)

        # Link SUPERSEDES
        self.repo.insert_edge(MemoryEdge.create(
            namespace=self.namespace,
            src=new_node,
            dst=old_node_id,
            relation=EdgeRelation.SUPERSEDES,
        ))

        # Mark old as superseded
        old = self.repo.get_node(old_node_id)
        if old:
            updated = replace(old, superseded_by=str(new_node), updated_at=utc_now())
            self.repo.update_node(updated)

        return new_node

    def add_edge(
            self,
            src: NodeId,
            dst: NodeId,
            relation: str,
            weight: float = 1.0,
    ) -> str:
        rel = EdgeRelation(relation)
        if rel not in EdgeRelation:
            raise InvalidMemoryRelation(f"Invalid relation: {relation}")

        # Verify both nodes exist and are in same namespace
        src_node = self.repo.get_node(src)
        dst_node = self.repo.get_node(dst)
        if not src_node or not dst_node:
            raise CrossNamespaceEdge("Edge endpoint does not exist")
        if src_node.namespace != self.namespace or dst_node.namespace != self.namespace:
            raise CrossNamespaceEdge("Cross-namespace edge not allowed")

        edge = MemoryEdge.create(
            namespace=self.namespace,
            src=src, dst=dst, relation=rel, weight=weight,
        )
        self.repo.insert_edge(edge)
        return str(edge.edge_id)


class MemoryReader(IMemoryReader):
    """Read operations for memory nodes."""

    def __init__(
            self,
            repo: IMemoryRepository,
            namespace: str,
            isolated: bool = False,
            shared_namespaces: tuple[str, ...] = (),
            tier: Tier = Tier.INTERNAL,
    ):
        self.repo = repo
        self.namespace = namespace
        self.isolated = isolated
        self.shared_namespaces = shared_namespaces
        self.tier = tier

    def _allowed_namespaces(self) -> list[str]:
        """Namespaces this graph can read from."""
        namespaces = [self.namespace]
        if not self.isolated:
            namespaces.extend(self.shared_namespaces)
        return namespaces

    def get_node(self, node_id: NodeId) -> MemoryNode | None:
        return self.repo.get_node(node_id)

    def list_nodes(
            self,
            namespace: str,
            memory_type: MemoryType | None = None,
            min_confidence: float = 0.0,
            include_superseded: bool = False,
            limit: int = 100,
    ) -> list[MemoryNode]:
        return self.repo.list_nodes(
            namespace=namespace,
            memory_type=memory_type,
            min_confidence=min_confidence,
            include_superseded=include_superseded,
            limit=limit,
        )

    def get_namespace_config(self) -> NamespaceConfig:
        return NamespaceConfig(
            name=self.namespace,
            isolated=self.isolated,
            tier=self.tier,
            shared_with=self.shared_namespaces,
        )


class MemoryDecay(IMemoryDecay):
    """Memory decay operations."""

    def __init__(self, repo: IMemoryRepository):
        self.repo = repo

    def decay_all(self, namespace: str) -> int:
        return self.repo.decay_all(namespace)


class MemoryGraphTraversal(IMemoryGraphTraversal):
    """Graph traversal and recall operations."""

    def __init__(
            self,
            repo: IMemoryRepository,
            namespace: str,
            embedding: IEmbeddingProvider | None = None,
            isolated: bool = False,
            shared_namespaces: tuple[str, ...] = (),
    ):
        self.repo = repo
        self.namespace = namespace
        self.embedding = embedding
        self.isolated = isolated
        self.shared_namespaces = shared_namespaces

    def _allowed_namespaces(self) -> list[str]:
        """Namespaces this graph can read from."""
        namespaces = [self.namespace]
        if not self.isolated:
            namespaces.extend(self.shared_namespaces)
        return namespaces

    def recall(
            self,
            query: str,
            depth: int = 2,
            top_k: int = 10,
            query_vector: list[float] | None = None,
            extra_namespaces: list[str] | None = None,
            memory_type: MemoryType | None = None,
    ) -> list[MemoryNode]:
        # Compute the query embedding ourselves when the caller didn't supply one.
        # No caller (the memory.search MCP tool included) actually computes a
        # query vector today, so without this the embedding-based recall branch
        # below was always a dead branch and recall silently degraded to
        # keyword-only even when an embedder is configured.
        if query_vector is None and self.embedding:
            try:
                query_vector = self.embedding.embed_query(query)
            except Exception:
                query_vector = None

        # Keyword recall
        seeds = self.repo.list_nodes(
            namespace=self.namespace,
            memory_type=memory_type,
            min_confidence=0.0,
            limit=top_k,
        )
        query_lower = query.lower()
        seeds = [n for n in seeds if query_lower in n.name.lower() or query_lower in json.dumps(n.body).lower()]

        # Embedding recall if available
        if query_vector and self.embedding:
            emb_seeds = self.repo.list_nodes(
                namespace=self.namespace,
                memory_type=memory_type,
                min_confidence=0.0,
                limit=top_k,
            )
            scored = []
            for n in emb_seeds:
                if n.embedding:
                    vec = list(struct.unpack(f"<{len(n.embedding) // 4}f", n.embedding))
                    # cosine similarity
                    dot = sum(a * b for a, b in zip(query_vector, vec))
                    norm_a = sum(x * x for x in query_vector) ** 0.5
                    norm_b = sum(x * x for x in vec) ** 0.5
                    if norm_a > 0 and norm_b > 0:
                        scored.append((n, dot / (norm_a * norm_b)))
            scored.sort(key=lambda x: x[1], reverse=True)
            for n, _ in scored[:top_k]:
                if n not in seeds:
                    seeds.append(n)

        # Expand graph
        seen = {n.node_id: n for n in seeds}
        seed_ids = list(seen.keys())
        expanded = self.repo.expand_graph(
            seed_ids=seed_ids,
            depth=depth,
            relations=None,
            namespace=self.namespace,
            extra_namespaces=extra_namespaces or self.shared_namespaces,
        )
        for n in expanded:
            seen.setdefault(n.node_id, n)

        # Rank by effective confidence
        result = list(seen.values())
        result.sort(key=lambda x: x.effective_confidence, reverse=True)
        return result[:top_k]

    def expand(
            self,
            seed_ids: list[NodeId],
            depth: int = 2,
            relations: list[str] | None = None,
            extra_namespaces: list[str] | None = None,
    ) -> list[MemoryNode]:
        return self.repo.expand_graph(
            seed_ids=seed_ids,
            depth=depth,
            relations=relations,
            namespace=self.namespace,
            extra_namespaces=extra_namespaces or self.shared_namespaces,
        )


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
