"""
claude-env :: Domain - Memory Entities - EdgeRelation
"""
from __future__ import annotations

from enum import Enum


class EdgeRelation(str, Enum):
    """Edge relation types."""
    RELATES_TO = "RELATES_TO"
    DEPENDS_ON = "DEPENDS_ON"
    DECISION_ABOUT = "DECISION_ABOUT"
    DISCOVERED_IN = "DISCOVERED_IN"
    SUPERSEDES = "SUPERSEDES"
    CONSOLIDATES = "CONSOLIDATES"
