"""
claude-env :: Domain - Memory Entities - Helpers

Groups: effective_confidence, validate_memory_kind.
"""
from __future__ import annotations

from datetime import datetime

from claudenv.domain.value_objects import utc_now
from claudenv.domain.memory.entities import MemoryType, NodeKind
from claudenv.domain.memory._kinds import VALID_KINDS
from claudenv.domain.memory.errors import InvalidMemoryKind


def effective_confidence(stored: float, half_life: float, updated_at: str | datetime) -> float:
    """Calculate time-decayed confidence."""
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age_days = max(0.0, (utc_now() - updated_at).total_seconds() / 86400)
    return stored * (2 ** (-age_days / max(1e-6, half_life)))


def validate_memory_kind(memory_type: MemoryType, node_kind: NodeKind) -> None:
    """Validate that (memory_type, node_kind) is a valid combination."""
    allowed = VALID_KINDS.get(memory_type)
    if allowed is None or node_kind not in allowed:
        raise InvalidMemoryKind(
            f"Invalid combination: memory_type={memory_type.value}, "
            f"node_kind={node_kind.value}. "
            f"Valid kinds for {memory_type.value}: {', '.join(k.value for k in allowed)}"
        )
