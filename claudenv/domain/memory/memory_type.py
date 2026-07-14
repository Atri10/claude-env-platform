"""
claude-env :: Domain - Memory Entities - MemoryType
"""
from __future__ import annotations

from enum import Enum


class MemoryType(str, Enum):
    """Top-level memory categories."""
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    PROCEDURAL = "procedural"
    AGENT = "agent"
