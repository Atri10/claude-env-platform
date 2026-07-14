"""
claude-env :: Application - Memory Operations

Thin application layer that re-exports domain memory services.
The actual implementation lives in claudenv.domain.memory.service.
"""
from __future__ import annotations

from claudenv.domain.memory.service import (
    MemoryGraph,
    MemoryServiceImpl as MemoryService,
    MemoryConsolidator,
    MemoryPruner,
    MemorySync,
    MemoryWriter,
    MemoryReader,
    MemoryDecay,
    MemoryGraphTraversal,
)

__all__ = [
    # Composite (backward compat)
    "MemoryGraph",
    "MemoryService",
    "MemoryConsolidator",
    "MemoryPruner",
    "MemorySync",
    # Focused ports
    "MemoryWriter",
    "MemoryReader",
    "MemoryDecay",
    "MemoryGraphTraversal",
]