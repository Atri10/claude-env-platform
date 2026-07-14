"""
claude-env :: Domain - RAG Entities - RetrievalResult
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claudenv.domain.rag.chunk import Chunk


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """A ranked retrieval result."""
    chunk: Chunk
    score: float
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.to_metadata(),
            "score": self.score,
            "rank": self.rank,
        }
