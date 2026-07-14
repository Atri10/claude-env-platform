"""
claude-env :: Domain - RAG Entities
"""
from __future__ import annotations

# Re-exported so `from claudenv.domain.rag import X` keeps working exactly
# as it did when rag.py imported these from value_objects directly.
from claudenv.domain.value_objects import (
    BranchName, ChunkId, ContentHash, RepoSlug, Tier, utc_now,
)

from claudenv.domain.rag.chunk_type import ChunkType
from claudenv.domain.rag.retrieval_mode import RetrievalMode
from claudenv.domain.rag.chunk import Chunk
from claudenv.domain.rag.retrieval_result import RetrievalResult
from claudenv.domain.rag.retrieval_query import RetrievalQuery
from claudenv.domain.rag.index_state import IndexState
from claudenv.domain.rag.file_state import FileState
from claudenv.domain.rag.rag_config import RAGConfig

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
