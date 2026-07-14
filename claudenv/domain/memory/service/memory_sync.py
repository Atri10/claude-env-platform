"""
claude-env :: Domain - Memory Service (Domain Layer) - MemorySync
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.memory import MemoryEdge, MemoryNode
from claudenv.domain.value_objects import utc_now
from claudenv.domain.memory.service.i_memory_repository import IMemoryRepository


class MemorySync:
    """Export/import memory namespaces for team sync."""

    def __init__(self, repo: IMemoryRepository, namespace: str):
        self.repo = repo
        self.namespace = namespace

    def export(self) -> list[dict[str, Any]]:
        """Export all nodes and edges in namespace."""
        nodes = self.repo.list_nodes(
            namespace=self.namespace,
            include_superseded=True,
            limit=10000,
        )
        edges = []
        for n in nodes:
            node_edges = self.repo.get_edges(src=n.node_id)
            edges.extend(node_edges)

        return {
            "namespace": self.namespace,
            "nodes": [n.to_dict() for n in nodes],
            "edges": [e.to_dict() for e in edges],
            "exported_at": utc_now().isoformat(),
        }

    def import_data(self, data: dict[str, Any]) -> int:
        """Import nodes and edges (additive, skips existing IDs)."""
        imported = 0
        for node_data in data.get("nodes", []):
            node = MemoryNode.from_dict(node_data)
            if not self.repo.get_node(node.node_id):
                self.repo.insert_node(node)
                imported += 1

        for edge_data in data.get("edges", []):
            edge = MemoryEdge.from_dict(edge_data)
            # Check if edge already exists
            existing = self.repo.get_edges(src=edge.src, dst=edge.dst)
            if not any(e.relation == edge.relation for e in existing):
                self.repo.insert_edge(edge)

        return imported
