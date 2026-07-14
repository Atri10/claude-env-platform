"""
claude-env :: Domain - RAG Entities
"""
from __future__ import annotations

from claudenv.domain.rag.config import RAGConfig
from claudenv.domain.rag.models import (
    Chunk,
    ChunkType,
    FileState,
    IndexState,
    RetrievalMode,
    RetrievalQuery,
    RetrievalResult,
)

# Re-exported so `from claudenv.domain.rag import X` keeps working exactly
# as it did when rag.py imported these from value_objects directly.
from claudenv.domain.value_objects import (
    BranchName,
    ChunkId,
    ContentHash,
    RepoSlug,
    Tier,
    utc_now,
)

__all__ = [
    "BranchName",
    "ChunkId",
    "ContentHash",
    "RepoSlug",
    "Tier",
    "utc_now",
    "ChunkType",
    "RetrievalMode",
    "Chunk",
    "RetrievalResult",
    "RetrievalQuery",
    "IndexState",
    "FileState",
    "RAGConfig",
]
