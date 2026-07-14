"""
claude-env :: Domain - Memory Service (Domain Layer) - Maintenance

Groups: MemoryConsolidator, MemoryPruner, MemorySync.
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.memory import EdgeRelation, MemoryEdge, MemoryNode, NodeKind
from claudenv.domain.memory.service.interfaces import IMemoryRepository
from claudenv.domain.value_objects import utc_now


class MemoryConsolidator:
    """Consolidate low-value memory clusters into summary nodes."""

    def __init__(self, repo: IMemoryRepository, namespace: str):
        self.repo = repo
        self.namespace = namespace

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        """Find and consolidate low-value clusters."""
        nodes = self.repo.list_nodes(
            namespace=self.namespace,
            min_confidence=0.0,
            include_superseded=False,
            limit=1000,
        )

        # Group by kind and look for clusters
        clusters: dict[str, list[MemoryNode]] = {}
        for n in nodes:
            key = f"{n.memory_type.value}:{n.node_kind.value}"
            clusters.setdefault(key, []).append(n)

        consolidated = 0
        for kind, cluster_nodes in clusters.items():
            if len(cluster_nodes) < 5:
                continue
            # Find low-confidence, old nodes
            candidates = [n for n in cluster_nodes if n.effective_confidence < 0.3]
            if len(candidates) < 3:
                continue

            # Create summary node
            summary_name = f"Consolidated {kind} ({len(candidates)} nodes)"
            summary_body = {
                "consolidated_from": [str(n.node_id) for n in candidates],
                "original_count": len(candidates),
                "consolidated_at": utc_now().isoformat(),
            }

            if not dry_run:
                new_id = MemoryNode.create(
                    namespace=self.namespace,
                    memory_type=candidates[0].memory_type,
                    node_kind=candidates[0].node_kind,
                    name=summary_name,
                    body=summary_body,
                    repo=candidates[0].repo,
                    confidence=0.5,
                ).node_id
                # Link CONSOLIDATES
                for c in candidates:
                    self.repo.insert_edge(MemoryEdge.create(
                        namespace=self.namespace,
                        src=new_id, dst=c.node_id,
                        relation=EdgeRelation.CONSOLIDATES,
                    ))
            consolidated += 1

        return {"consolidated_clusters": consolidated, "dry_run": dry_run}


class MemoryPruner:
    """Prune low-value, superseded, or expired memories."""

    def __init__(self, repo: IMemoryRepository, namespace: str):
        self.repo = repo
        self.namespace = namespace

    def run(self, dry_run: bool = True) -> dict[str, Any]:
        """Prune low-confidence, superseded, or expired nodes."""
        nodes = self.repo.list_nodes(
            namespace=self.namespace,
            min_confidence=0.0,
            include_superseded=True,
            limit=5000,
        )

        to_prune = []
        for n in nodes:
            # Never prune decision/architecture nodes
            if n.node_kind in (NodeKind.DECISION, NodeKind.ARCHITECTURE):
                continue
            # Prune superseded
            if n.is_superseded():
                to_prune.append(n)
            # Prune very low confidence old nodes
            elif n.effective_confidence < 0.1 and n.node_kind not in (NodeKind.DECISION, NodeKind.ARCHITECTURE):
                to_prune.append(n)

        pruned = 0
        if not dry_run:
            for n in to_prune:
                # In production, would archive first
                self.repo.delete_node(n.node_id)
                pruned += 1

        return {"candidates": len(to_prune), "pruned": pruned, "dry_run": dry_run}


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
