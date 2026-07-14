"""
claude-env :: Domain - Memory Service (Domain Layer) - MemoryServiceImpl
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.memory.service.interfaces import (
    IConfigProvider,
    IMemoryGraph,
    IMemoryRepository,
)
from claudenv.domain.memory.service.maintenance import MemoryConsolidator, MemoryPruner, MemorySync
from claudenv.domain.memory.service.services import (
    MemoryDecay,
    MemoryGraph,
    MemoryGraphTraversal,
    MemoryReader,
    MemoryWriter,
)
from claudenv.domain.value_objects import RepoSlug, Tier


class MemoryServiceImpl:
    """Domain implementation of the memory service port."""

    def __init__(
            self,
            repo: IMemoryRepository,
            config: IConfigProvider,
    ):
        self.repo = repo
        self.config = config

    def create_graph(
            self,
            namespace: str,
            isolated: bool = False,
            tier: Tier = Tier.INTERNAL,
            shared_with: tuple[str, ...] = (),
    ) -> IMemoryGraph:
        embedding = None
        try:
            from claudenv.adapters.embedding import get_embedder
            embedding = get_embedder()
        except Exception:
            pass

        return MemoryGraph(
            repo=self.repo,
            namespace=namespace,
            embedding=embedding,
            isolated=isolated,
            shared_namespaces=shared_with,
            tier=tier,
        )

    def get_project_graph(self, repo_slug: RepoSlug, tier: Tier) -> IMemoryGraph:
        isolated = tier >= Tier.SENSITIVE
        return self.create_graph(
            namespace=f"proj-{repo_slug}",
            isolated=isolated,
            tier=tier,
        )

    def get_agent_graph(self, agent_id: str) -> IMemoryGraph:
        return self.create_graph(
            namespace=f"agent:{agent_id}",
            isolated=True,
            tier=Tier.INTERNAL,
        )

    def consolidate(self, namespace: str, dry_run: bool = False) -> dict[str, Any]:
        """Run memory consolidation."""
        consolidator = MemoryConsolidator(self.repo, namespace)
        return consolidator.run(dry_run=dry_run)

    def prune(self, namespace: str, dry_run: bool = True) -> dict[str, Any]:
        """Run memory pruning."""
        pruner = MemoryPruner(self.repo, namespace)
        return pruner.run(dry_run=dry_run)

    def sync_export(self, namespace: str) -> list[dict[str, Any]]:
        """Export memory namespace."""
        sync = MemorySync(self.repo, namespace)
        return sync.export()

    def sync_import(self, namespace: str, data: list[dict[str, Any]]) -> int:
        """Import memory namespace."""
        sync = MemorySync(self.repo, namespace)
        return sync.import_data(data)

    # --- Focused Service Factories ---
    def create_writer(self, namespace: str) -> MemoryWriter:
        """Create a focused MemoryWriter for the namespace."""
        embedding = None
        try:
            from claudenv.adapters.embedding import get_embedder
            embedding = get_embedder()
        except Exception:
            pass
        return MemoryWriter(self.repo, namespace, embedding)

    def create_reader(
            self,
            namespace: str,
            isolated: bool = False,
            shared_with: tuple[str, ...] = (),
            tier: Tier = Tier.INTERNAL,
    ) -> MemoryReader:
        """Create a focused MemoryReader for the namespace."""
        return MemoryReader(self.repo, namespace, isolated, shared_with, tier)

    def create_decay(self) -> MemoryDecay:
        """Create a focused MemoryDecay service."""
        return MemoryDecay(self.repo)

    def create_traversal(
            self,
            namespace: str,
            isolated: bool = False,
            shared_with: tuple[str, ...] = (),
    ) -> MemoryGraphTraversal:
        """Create a focused MemoryGraphTraversal for the namespace."""
        embedding = None
        try:
            from claudenv.adapters.embedding import get_embedder
            embedding = get_embedder()
        except Exception:
            pass
        return MemoryGraphTraversal(self.repo, namespace, embedding, isolated, shared_with)
