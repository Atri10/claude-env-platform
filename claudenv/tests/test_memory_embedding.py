"""
Tests for claudenv application memory embedding wiring.

MemoryGraph.add_node() used to call self.embedding.embed_text(text) -- but
the real embedding provider (claudenv/adapters/embedding.py's EmbedderBackend,
returned by get_embedder()) only exposes embed_documents()/embed_query(), not
embed_text()/embed_batch() (those names only ever existed on the stale
IEmbeddingProvider Protocol in ports/memory.py, which nothing implements).
The AttributeError was swallowed by a bare `except Exception: pass`, so every
node was silently stored with no embedding and semantic recall never worked.

Similarly, recall() accepted a `query_vector` parameter for embedding-based
recall, but no caller (the memory.search MCP tool included) ever computed
one, so that whole code path was dead.
"""
from __future__ import annotations

import struct

from claudenv.application.memory import MemoryGraph
from claudenv.domain.memory import MemoryNode, MemoryType, NodeKind


class FakeEmbedder:
    """Mirrors the real EmbedderBackend's interface (embed_documents/embed_query),
    not the stale embed_text/embed_batch Protocol."""

    def __init__(self, dim: int = 4):
        self.dim = dim
        self.embed_documents_calls: list[list[str]] = []
        self.embed_query_calls: list[str] = []

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.embed_documents_calls.append(texts)
        return [[float(len(t))] * self.dim for t in texts]

    def embed_query(self, text: str) -> list[float]:
        self.embed_query_calls.append(text)
        return [float(len(text))] * self.dim


class FakeMemoryRepository:
    def __init__(self):
        self.inserted: list[MemoryNode] = []

    def insert_node(self, node: MemoryNode) -> None:
        self.inserted.append(node)

    def list_nodes(self, namespace, memory_type=None, min_confidence=0.0,
                   include_superseded=False, limit=100):
        return [n for n in self.inserted if n.namespace == namespace]

    def expand_graph(self, seed_ids, depth, relations, namespace, extra_namespaces):
        return []


class TestAddNodeEmbedding:
    def test_add_node_uses_embed_documents_not_embed_text(self):
        embedder = FakeEmbedder(dim=3)
        repo = FakeMemoryRepository()
        graph = MemoryGraph(repo, "proj-test", embedding=embedder)

        graph.add_node(MemoryType.SEMANTIC, "entity", "widget", {"summary": "a widget"})

        assert len(repo.inserted) == 1
        node = repo.inserted[0]
        assert node.embedding is not None
        # Packed as little-endian float32s so recall() can struct.unpack it.
        vec = list(struct.unpack(f"<{len(node.embedding) // 4}f", node.embedding))
        assert len(vec) == 3
        assert embedder.embed_documents_calls  # embed_documents was actually called

    def test_add_node_survives_embedder_failure(self):
        class BrokenEmbedder:
            def embed_documents(self, texts):
                raise RuntimeError("model not loaded")

        repo = FakeMemoryRepository()
        graph = MemoryGraph(repo, "proj-test", embedding=BrokenEmbedder())

        node_id = graph.add_node(MemoryType.SEMANTIC, "entity", "widget", {})
        assert node_id is not None
        assert repo.inserted[0].embedding is None


class TestRecallComputesQueryVector:
    def test_recall_computes_query_vector_when_embedder_configured(self):
        embedder = FakeEmbedder()
        repo = FakeMemoryRepository()
        graph = MemoryGraph(repo, "proj-test", embedding=embedder)

        graph.recall(query="widgets")

        assert embedder.embed_query_calls == ["widgets"]

    def test_recall_does_not_compute_query_vector_without_embedder(self):
        repo = FakeMemoryRepository()
        graph = MemoryGraph(repo, "proj-test", embedding=None)

        # Must not raise (no embedder configured -> keyword-only recall).
        results = graph.recall(query="widgets")
        assert results == []

    def test_recall_uses_caller_supplied_query_vector_if_given(self):
        embedder = FakeEmbedder()
        repo = FakeMemoryRepository()
        graph = MemoryGraph(repo, "proj-test", embedding=embedder)

        graph.recall(query="widgets", query_vector=[1.0, 2.0, 3.0])

        # Caller already supplied a vector -- must not recompute it.
        assert embedder.embed_query_calls == []
