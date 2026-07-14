"""
claude-env :: Domain - Memory Entities - InvalidMemoryKind
"""
from __future__ import annotations


class InvalidMemoryKind(ValueError):
    """Raised when (memory_type, node_kind) isn't in the documented taxonomy."""
