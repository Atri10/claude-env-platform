"""
claude-env :: Domain - RAG Entities - ChunkType
"""
from __future__ import annotations

from enum import Enum


class ChunkType(str, Enum):
    """Types of code chunks."""
    FUNCTION = "function"
    CLASS = "class"
    SECTION = "section"
    WINDOW = "window"
