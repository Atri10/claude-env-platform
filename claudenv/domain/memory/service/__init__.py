"""
claude-env :: Domain - Memory Service (Domain Layer)

This module contains the core domain logic for memory operations. It lives in
the domain layer (not the application layer) so that domain/policy.py can depend
on it without a cycle through the application package.

The domain layer must not depend on the application layer or ports package.
"""
from __future__ import annotations

from claudenv.domain.memory.service.interfaces import (
    IConfigProvider,
    IEmbeddingProvider,
    IMemoryDecay,
    IMemoryGraph,
    IMemoryGraphTraversal,
    IMemoryReader,
    IMemoryRepository,
    IMemoryService,
    IMemoryWriter,
)
from claudenv.domain.memory.service.maintenance import MemoryConsolidator, MemoryPruner, MemorySync
from claudenv.domain.memory.service.memory_service_impl import MemoryServiceImpl
from claudenv.domain.memory.service.services import (
    MemoryDecay,
    MemoryGraph,
    MemoryGraphTraversal,
    MemoryReader,
    MemoryWriter,
)

__all__ = [
    "IMemoryRepository",
    "IMemoryWriter",
    "IMemoryReader",
    "IMemoryDecay",
    "IMemoryGraphTraversal",
    "IMemoryGraph",
    "IEmbeddingProvider",
    "IConfigProvider",
    "MemoryWriter",
    "MemoryReader",
    "MemoryDecay",
    "MemoryGraphTraversal",
    "MemoryGraph",
    "IMemoryService",
    "MemoryServiceImpl",
    "MemoryConsolidator",
    "MemoryPruner",
    "MemorySync",
]
