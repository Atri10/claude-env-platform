"""
claude-env :: Ports - Memory Interfaces

The memory ports are consumer-owned by the domain layer and are defined there
(see claudenv.domain.memory.service). This module re-exports them so the ports
package remains the single import surface for application/adapters code. The
domain layer must not depend on this package, so the definitions live in
claudenv.domain.memory.service and are merely re-exported here.
"""
from __future__ import annotations

from claudenv.domain.memory.service import (
    IEmbeddingProvider,
    IMemoryDecay,
    IMemoryGraph,
    IMemoryGraphTraversal,
    IMemoryReader,
    IMemoryRepository,
    IMemoryService,
    IMemoryWriter,
    MemoryConsolidator,
    MemoryDecay,
    MemoryGraph,
    MemoryGraphTraversal,
    MemoryPruner,
    MemoryReader,
    MemoryServiceImpl,
    MemorySync,
    MemoryWriter,
)

__all__ = [
    # Consumer-owned interfaces (defined in claudenv.domain.memory.service)
    "IMemoryGraph",
    "IMemoryRepository",
    "IEmbeddingProvider",
    "IMemoryService",
    "IMemoryWriter",
    "IMemoryReader",
    "IMemoryDecay",
    "IMemoryGraphTraversal",
    # Concrete domain implementations
    "MemoryGraph",
    "MemoryServiceImpl",
    "MemoryWriter",
    "MemoryReader",
    "MemoryDecay",
    "MemoryGraphTraversal",
    "MemoryConsolidator",
    "MemoryPruner",
    "MemorySync",
]
