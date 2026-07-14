"""
claude-env :: Domain - Memory Entities - InvalidMemoryRelation
"""
from __future__ import annotations


class InvalidMemoryRelation(ValueError):
    """Raised when an edge relation isn't in the documented vocabulary."""
