"""
Tests for claudenv application memory recall — specifically the
memory_type filter, which was previously parsed by the MCP server but
silently dropped before reaching the repository query.
"""
from __future__ import annotations

from claudenv.domain.memory import MemoryNode, MemoryType, NodeKind
from claudenv.domain.memory.service import MemoryGraph


class FakeMemoryRepository:
    """Minimal IMemoryRepository fake that records the memory_type it was
    filtered by, so tests can assert recall() actually threads it through."""

    def __init__(self, nodes: list[MemoryNode]):
        self._nodes = nodes
        self.last_list_nodes_memory_type = "NOT_CALLED"

    def list_nodes(self, namespace, memory_type=None, min_confidence=0.0,
                   include_superseded=False, limit=100):
        self.last_list_nodes_memory_type = memory_type
        nodes = [n for n in self._nodes if n.namespace == namespace]
        if memory_type:
            nodes = [n for n in nodes if n.memory_type == memory_type]
        return nodes[:limit]

    def expand_graph(self, seed_ids, depth, relations, namespace, extra_namespaces):
        return []


def _node(namespace: str, memory_type: MemoryType, name: str) -> MemoryNode:
    return MemoryNode.create(
        namespace=namespace,
        memory_type=memory_type,
        node_kind=NodeKind.ENTITY if memory_type == MemoryType.SEMANTIC else NodeKind.DECISION,
        name=name,
        body={"content": name},
    )


class TestRecallMemoryTypeFilter:
    def test_recall_without_filter_passes_none(self):
        repo = FakeMemoryRepository([])
        graph = MemoryGraph(repo, "proj-test")
        graph.recall(query="anything")
        assert repo.last_list_nodes_memory_type is None

    def test_recall_with_filter_threads_it_to_repository(self):
        repo = FakeMemoryRepository([])
        graph = MemoryGraph(repo, "proj-test")
        graph.recall(query="anything", memory_type=MemoryType.SEMANTIC)
        assert repo.last_list_nodes_memory_type == MemoryType.SEMANTIC

    def test_recall_filter_actually_excludes_other_types(self):
        nodes = [
            _node("proj-test", MemoryType.SEMANTIC, "fact about widgets"),
            _node("proj-test", MemoryType.EPISODIC, "decision about widgets"),
        ]
        repo = FakeMemoryRepository(nodes)
        graph = MemoryGraph(repo, "proj-test")

        results = graph.recall(query="widgets", memory_type=MemoryType.SEMANTIC)

        assert len(results) == 1
        assert results[0].memory_type == MemoryType.SEMANTIC

    def test_recall_without_filter_sees_both_types(self):
        nodes = [
            _node("proj-test", MemoryType.SEMANTIC, "fact about widgets"),
            _node("proj-test", MemoryType.EPISODIC, "decision about widgets"),
        ]
        repo = FakeMemoryRepository(nodes)
        graph = MemoryGraph(repo, "proj-test")

        results = graph.recall(query="widgets")

        assert {n.memory_type for n in results} == {MemoryType.SEMANTIC, MemoryType.EPISODIC}
