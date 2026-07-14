"""
claude-env :: Domain - Memory Service (Domain Layer) - IMemoryGraphTraversal
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.memory import MemoryNode, MemoryType
from claudenv.domain.value_objects import NodeId


class IMemoryGraphTraversal(Protocol):
    """Graph traversal and recall operations."""

    @abstractmethod
    def recall(
            self,
            query: str,
            depth: int = 2,
            top_k: int = 10,
            query_vector: list[float] | None = None,
            extra_namespaces: list[str] | None = None,
            memory_type: MemoryType | None = None,
    ) -> list[MemoryNode]:
        ...

    @abstractmethod
    def expand(
            self,
            seed_ids: list[NodeId],
            depth: int = 2,
            relations: list[str] | None = None,
            extra_namespaces: list[str] | None = None,
    ) -> list[MemoryNode]:
        ...
