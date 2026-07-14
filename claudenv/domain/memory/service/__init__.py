"""
claude-env :: Domain - Memory Service (Domain Layer)

This module contains the core domain logic for memory operations.
It was moved from application/memory.py to break the circular dependency
where domain/policy.py -> application.memory -> domain/memory.

The domain layer must not depend on the application layer or ports package.
"""
from __future__ import annotations

from claudenv.domain.memory.service.i_memory_repository import IMemoryRepository
from claudenv.domain.memory.service.i_memory_writer import IMemoryWriter
from claudenv.domain.memory.service.i_memory_reader import IMemoryReader
from claudenv.domain.memory.service.i_memory_decay import IMemoryDecay
from claudenv.domain.memory.service.i_memory_graph_traversal import IMemoryGraphTraversal
from claudenv.domain.memory.service.i_memory_graph import IMemoryGraph
from claudenv.domain.memory.service.i_embedding_provider import IEmbeddingProvider
from claudenv.domain.memory.service.i_config_provider import IConfigProvider
from claudenv.domain.memory.service.memory_writer import MemoryWriter
from claudenv.domain.memory.service.memory_reader import MemoryReader
from claudenv.domain.memory.service.memory_decay import MemoryDecay
from claudenv.domain.memory.service.memory_graph_traversal import MemoryGraphTraversal
from claudenv.domain.memory.service.memory_graph import MemoryGraph
from claudenv.domain.memory.service.i_memory_service import IMemoryService
from claudenv.domain.memory.service.memory_service_impl import MemoryServiceImpl
from claudenv.domain.memory.service.memory_consolidator import MemoryConsolidator
from claudenv.domain.memory.service.memory_pruner import MemoryPruner
from claudenv.domain.memory.service.memory_sync import MemorySync

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
