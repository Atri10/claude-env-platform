"""
claude-env :: Domain - Memory Service (Domain Layer)

This module contains the core domain logic for memory operations. It lives in
the domain layer (not the application layer) so that domain/policy.py can depend
on it without a cycle through the application package.

The domain layer must not depend on the application layer or ports package.
"""
from __future__ import annotations

from claudenv.domain.memory.service.interfaces import (
    IMemoryRepository,
    IMemoryWriter,
    IMemoryReader,
    IMemoryDecay,
    IMemoryGraphTraversal,
    IMemoryGraph,
    IEmbeddingProvider,
    IConfigProvider,
    IMemoryService,
)
from claudenv.domain.memory.service.services import (
    MemoryWriter,
    MemoryReader,
    MemoryDecay,
    MemoryGraphTraversal,
    MemoryGraph,
)
from claudenv.domain.memory.service.memory_service_impl import MemoryServiceImpl
from claudenv.domain.memory.service.maintenance import MemoryConsolidator, MemoryPruner, MemorySync

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
