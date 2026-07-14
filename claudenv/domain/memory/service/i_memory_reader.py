"""
claude-env :: Domain - Memory Service (Domain Layer) - IMemoryReader
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.memory import MemoryNode, MemoryType, NamespaceConfig
from claudenv.domain.value_objects import NodeId


class IMemoryReader(Protocol):
    """Read operations for memory nodes."""

    @abstractmethod
    def get_node(self, node_id: NodeId) -> MemoryNode | None:
        ...

    @abstractmethod
    def list_nodes(
            self,
            namespace: str,
            memory_type: MemoryType | None = None,
            min_confidence: float = 0.0,
            include_superseded: bool = False,
            limit: int = 100,
    ) -> list[MemoryNode]:
        ...

    @abstractmethod
    def get_namespace_config(self) -> NamespaceConfig:
        ...
