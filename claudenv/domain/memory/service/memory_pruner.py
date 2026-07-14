"""
claude-env :: Domain - Memory Service (Domain Layer) - MemoryPruner
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.memory import NodeKind
from claudenv.domain.memory.service.i_memory_repository import IMemoryRepository


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
