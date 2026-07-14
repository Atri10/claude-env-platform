"""
claude-env :: Domain - RAG Entities - RetrievalMode
"""
from __future__ import annotations

from enum import Enum


class RetrievalMode(str, Enum):
    """Retrieval modes."""
    VECTOR = "vector"
    FTS = "fts"
    HYBRID = "hybrid"
