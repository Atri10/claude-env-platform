"""
claude-env :: Domain - RAG Chunking Strategies - MarkdownChunkConfig
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MarkdownChunkConfig:
    """Configuration for markdown chunking."""
    max_chunk_chars: int = 3000
    overlap_chars: int = 200
    min_header_level: int = 1
    max_header_level: int = 3
