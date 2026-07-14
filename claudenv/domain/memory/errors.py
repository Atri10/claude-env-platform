"""
claude-env :: Domain - Memory Entities - Errors

Groups: InvalidMemoryKind, InvalidMemoryRelation, CrossNamespaceEdge.
"""
from __future__ import annotations


class InvalidMemoryKind(ValueError):
    """Raised when (memory_type, node_kind) isn't in the documented taxonomy."""


class InvalidMemoryRelation(ValueError):
    """Raised when an edge relation isn't in the documented vocabulary."""


class CrossNamespaceEdge(ValueError):
    """Raised when an edge would join nodes across namespaces (isolation leak)."""
