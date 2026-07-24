"""claude-env :: Domain - Memory - Traversal"""
from __future__ import annotations

import json
import logging
import struct

from claudenv.domain.memory import MemoryNode, MemoryType
from claudenv.domain.memory.service.interfaces import (
    IEmbeddingProvider,
    IMemoryGraphTraversal,
    IMemoryRepository,
)
from claudenv.domain.value_objects import NodeId

logger = logging.getLogger(__name__)


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
                logger.warning("query embedding failed; falling back to keyword recall", exc_info=True)
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
