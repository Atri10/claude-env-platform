"""
claude-env :: Domain - Memory Entities
"""
from __future__ import annotations

# Re-exported so `from claudenv.domain.memory import X` keeps working exactly
# as it did when memory.py imported these from value_objects directly.
from claudenv.domain.value_objects import (
    EdgeId, NodeId, RepoSlug, Tier, utc_now,
)

from claudenv.domain.memory.entities import MemoryType, NodeKind, EdgeRelation, MemoryNode, MemoryEdge
from claudenv.domain.memory._kinds import VALID_KINDS, HALF_LIFE_DAYS
from claudenv.domain.memory.namespace import Namespace, NamespaceConfig
from claudenv.domain.memory.errors import InvalidMemoryKind, InvalidMemoryRelation, CrossNamespaceEdge
from claudenv.domain.memory.helpers import effective_confidence, validate_memory_kind

# Re-export from service module for backward compatibility
from claudenv.domain.memory.service import MemoryGraph, MemoryServiceImpl, IMemoryService  # noqa: F401,E402

__all__ = [
    "EdgeId",
    "NodeId",
    "RepoSlug",
    "Tier",
    "utc_now",
    "effective_confidence",
    "MemoryType",
    "NodeKind",
    "EdgeRelation",
    "VALID_KINDS",
    "HALF_LIFE_DAYS",
    "MemoryNode",
    "MemoryEdge",
    "Namespace",
    "InvalidMemoryKind",
    "InvalidMemoryRelation",
    "CrossNamespaceEdge",
    "validate_memory_kind",
    "NamespaceConfig",
    "MemoryGraph",
    "MemoryServiceImpl",
    "IMemoryService",
]
