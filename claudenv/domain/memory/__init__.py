"""
claude-env :: Domain - Memory Entities
"""
from __future__ import annotations

# Re-exported so `from claudenv.domain.memory import X` keeps working exactly
# as it did when memory.py imported these from value_objects directly.
from claudenv.domain.value_objects import (
    EdgeId, NodeId, RepoSlug, Tier, utc_now,
)

from claudenv.domain.memory.effective_confidence import effective_confidence
from claudenv.domain.memory.memory_type import MemoryType
from claudenv.domain.memory.node_kind import NodeKind
from claudenv.domain.memory.edge_relation import EdgeRelation
from claudenv.domain.memory._kinds import VALID_KINDS, HALF_LIFE_DAYS
from claudenv.domain.memory.memory_node import MemoryNode
from claudenv.domain.memory.memory_edge import MemoryEdge
from claudenv.domain.memory.namespace import Namespace
from claudenv.domain.memory.invalid_memory_kind import InvalidMemoryKind
from claudenv.domain.memory.invalid_memory_relation import InvalidMemoryRelation
from claudenv.domain.memory.cross_namespace_edge import CrossNamespaceEdge
from claudenv.domain.memory.validate_memory_kind import validate_memory_kind
from claudenv.domain.memory.namespace_config import NamespaceConfig

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
