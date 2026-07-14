"""
claude-env :: Domain - Memory Entities
"""
from __future__ import annotations

from claudenv.domain.memory._kinds import HALF_LIFE_DAYS, VALID_KINDS
from claudenv.domain.memory.entities import (
    EdgeRelation,
    MemoryEdge,
    MemoryNode,
    MemoryType,
    NodeKind,
)
from claudenv.domain.memory.errors import (
    CrossNamespaceEdge,
    InvalidMemoryKind,
    InvalidMemoryRelation,
)
from claudenv.domain.memory.helpers import effective_confidence, validate_memory_kind
from claudenv.domain.memory.namespace import Namespace, NamespaceConfig

# Re-export from service module for backward compatibility
from claudenv.domain.memory.service import (  # noqa: F401,E402
    IMemoryService,
    MemoryGraph,
    MemoryServiceImpl,
)

# Re-exported so `from claudenv.domain.memory import X` keeps working exactly
# as it did when memory.py imported these from value_objects directly.
from claudenv.domain.value_objects import (
    EdgeId,
    NodeId,
    RepoSlug,
    Tier,
    utc_now,
)

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
