"""
claude-env :: Domain - Memory Service (Domain Layer) - MemoryConsolidator

Memory Maintenance Operations (Domain Layer)
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.memory import EdgeRelation, MemoryEdge, MemoryNode
from claudenv.domain.value_objects import utc_now
from claudenv.domain.memory.service.i_memory_repository import IMemoryRepository


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
