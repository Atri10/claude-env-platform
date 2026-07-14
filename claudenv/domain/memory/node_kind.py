"""
claude-env :: Domain - Memory Entities - NodeKind
"""
from __future__ import annotations

from enum import Enum


class NodeKind(str, Enum):
    """Specific node kinds per memory type."""
    # Episodic
    SESSION = "session"
    DECISION = "decision"
    INVESTIGATION = "investigation"
    # Semantic
    ENTITY = "entity"
    CONCEPT = "concept"
    ARCHITECTURE = "architecture"
    PREFERENCE = "preference"
    # Procedural
    WORKFLOW = "workflow"
    CONVENTION = "convention"
    PATTERN = "pattern"
