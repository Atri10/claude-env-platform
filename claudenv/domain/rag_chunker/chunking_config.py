"""
claude-env :: Domain - RAG Chunking Strategies - ChunkingConfig

RAGConfig extension for chunking params (used by factory).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkingConfig:
    """Chunking configuration (subset of RAGConfig used by chunkers)."""
    chunk_target_tokens: int = 512
    chunk_overlap_tokens: int = 64

    @property
    def target_chars(self) -> int:
        return self.chunk_target_tokens * 4  # ~4 chars/token

    @property
    def overlap_chars(self) -> int:
        return self.chunk_overlap_tokens * 4
