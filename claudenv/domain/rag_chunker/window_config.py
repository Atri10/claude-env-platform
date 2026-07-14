"""
claude-env :: Domain - RAG Chunking Strategies - WindowConfig
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WindowConfig:
    """Configuration for sliding window chunking."""
    target_chars: int = 2000  # ~500 tokens
    overlap_chars: int = 200  # ~50 tokens overlap
    min_chars: int = 100
