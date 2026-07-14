"""
claude-env :: Domain - Memory Entities - validate_memory_kind
"""
from __future__ import annotations

from claudenv.domain.memory.memory_type import MemoryType
from claudenv.domain.memory.node_kind import NodeKind
from claudenv.domain.memory._kinds import VALID_KINDS
from claudenv.domain.memory.invalid_memory_kind import InvalidMemoryKind


def validate_memory_kind(memory_type: MemoryType, node_kind: NodeKind) -> None:
    """Validate that (memory_type, node_kind) is a valid combination."""
    allowed = VALID_KINDS.get(memory_type)
    if allowed is None or node_kind not in allowed:
        raise InvalidMemoryKind(
            f"Invalid combination: memory_type={memory_type.value}, "
            f"node_kind={node_kind.value}. "
            f"Valid kinds for {memory_type.value}: {', '.join(k.value for k in allowed)}"
        )
