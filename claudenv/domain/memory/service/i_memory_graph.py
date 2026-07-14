"""
claude-env :: Domain - Memory Service (Domain Layer) - IMemoryGraph
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.memory.service.i_memory_writer import IMemoryWriter
from claudenv.domain.memory.service.i_memory_reader import IMemoryReader
from claudenv.domain.memory.service.i_memory_graph_traversal import IMemoryGraphTraversal


class IMemoryGraph(IMemoryWriter, IMemoryReader, IMemoryGraphTraversal, Protocol):
    """High-level memory graph operations (composite of focused ports)."""

    @abstractmethod
    def decay(self) -> int:
        ...

    # All other methods inherited from focused ports
