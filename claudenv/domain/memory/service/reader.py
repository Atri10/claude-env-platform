"""claude-env :: Domain - Memory - Reader"""
from __future__ import annotations

from claudenv.domain.memory import MemoryNode, MemoryType, NamespaceConfig
from claudenv.domain.memory.service.interfaces import IMemoryReader, IMemoryRepository
from claudenv.domain.value_objects import NodeId, Tier


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
