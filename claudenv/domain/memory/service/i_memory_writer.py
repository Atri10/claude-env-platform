"""
claude-env :: Domain - Memory Service (Domain Layer) - IMemoryWriter
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.memory import MemoryType
from claudenv.domain.value_objects import NodeId


class IMemoryWriter(Protocol):
    """Write operations for memory nodes."""

    @abstractmethod
    def add_node(
            self,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            repo: str | None = None,
            confidence: float = 1.0,
            embedding: bytes | None = None,
    ) -> NodeId:
        ...

    @abstractmethod
    def supersede(
            self,
            old_node_id: NodeId,
            memory_type: MemoryType,
            node_kind: str,
            name: str,
            body: dict[str, Any],
            **kwargs,
    ) -> NodeId:
        ...

    @abstractmethod
    def add_edge(
            self,
            src: NodeId,
            dst: NodeId,
            relation: str,
            weight: float = 1.0,
    ) -> str:
        ...
