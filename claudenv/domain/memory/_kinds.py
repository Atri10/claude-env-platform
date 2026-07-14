"""
claude-env :: Domain - Memory Entities - shared taxonomy constants

VALID_KINDS and HALF_LIFE_DAYS are constants (not classes) tightly coupled to
MemoryType/NodeKind; kept together here to avoid one-constant-per-file noise.
"""
from __future__ import annotations

from claudenv.domain.memory.entities import MemoryType, NodeKind

# Valid (memory_type, node_kind) combinations
VALID_KINDS: dict[MemoryType, set[NodeKind]] = {
    MemoryType.EPISODIC: {NodeKind.SESSION, NodeKind.DECISION, NodeKind.INVESTIGATION},
    MemoryType.SEMANTIC: {NodeKind.ENTITY, NodeKind.CONCEPT, NodeKind.ARCHITECTURE, NodeKind.PREFERENCE},
    MemoryType.PROCEDURAL: {NodeKind.WORKFLOW, NodeKind.CONVENTION, NodeKind.PATTERN},
    MemoryType.AGENT: {  # Agent namespace reuses any kind
        NodeKind.SESSION, NodeKind.DECISION, NodeKind.INVESTIGATION,
        NodeKind.ENTITY, NodeKind.CONCEPT, NodeKind.ARCHITECTURE, NodeKind.PREFERENCE,
        NodeKind.WORKFLOW, NodeKind.CONVENTION, NodeKind.PATTERN,
    },
}

# Half-life in days per node kind
HALF_LIFE_DAYS: dict[NodeKind, int] = {
    NodeKind.SESSION: 30,
    NodeKind.INVESTIGATION: 30,
    NodeKind.DECISION: 365,
    NodeKind.ENTITY: 180,
    NodeKind.CONCEPT: 270,
    NodeKind.ARCHITECTURE: 365,
    NodeKind.PREFERENCE: 270,
    NodeKind.WORKFLOW: 540,
    NodeKind.CONVENTION: 540,
    NodeKind.PATTERN: 365,
}
