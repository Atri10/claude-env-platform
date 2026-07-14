"""
claude-env :: Domain - Memory Entities - CrossNamespaceEdge
"""
from __future__ import annotations


class CrossNamespaceEdge(ValueError):
    """Raised when an edge would join nodes across namespaces (isolation leak)."""
