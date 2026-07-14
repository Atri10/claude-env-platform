"""
claude-env :: Domain - Memory Service (Domain Layer) - IMemoryService
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claudenv.domain.value_objects import RepoSlug, Tier
from claudenv.domain.memory.service.i_memory_graph import IMemoryGraph


@dataclass
class IMemoryService:
    """Port for memory service operations (consumer-owned interface).

    This is the interface that application-layer code depends on.
    The domain layer provides the implementation (MemoryServiceImpl).
    """

    def create_graph(
            self,
            namespace: str,
            isolated: bool = False,
            tier: Tier = Tier.INTERNAL,
            shared_with: tuple[str, ...] = (),
    ) -> IMemoryGraph:
        """Create a memory graph for the given namespace."""
        ...

    def get_project_graph(self, repo_slug: RepoSlug, tier: Tier) -> IMemoryGraph:
        """Get a project-scoped memory graph."""
        ...

    def get_agent_graph(self, agent_id: str) -> IMemoryGraph:
        """Get an agent-scoped memory graph."""
        ...

    def consolidate(self, namespace: str, dry_run: bool = False) -> dict[str, Any]:
        """Run memory consolidation."""
        ...

    def prune(self, namespace: str, dry_run: bool = True) -> dict[str, Any]:
        """Run memory pruning."""
        ...

    def sync_export(self, namespace: str) -> list[dict[str, Any]]:
        """Export memory namespace."""
        ...

    def sync_import(self, namespace: str, data: list[dict[str, Any]]) -> int:
        """Import memory namespace."""
        ...
