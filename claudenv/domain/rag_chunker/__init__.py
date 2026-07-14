"""
claude-env :: Domain - RAG Chunking Strategies
Pure domain logic for chunking source files — no I/O, no external deps.
"""
from __future__ import annotations

# Re-exported so `from claudenv.domain.rag_chunker import X` keeps working
# exactly as it did when rag_chunker.py imported these from claudenv.domain.rag.
from claudenv.domain.rag import Chunk, ChunkId, ChunkType, RepoSlug, BranchName, ContentHash, Tier

from claudenv.domain.rag_chunker.i_chunker import IChunker
from claudenv.domain.rag_chunker.window_config import WindowConfig
from claudenv.domain.rag_chunker.sliding_window_chunks import sliding_window_chunks
from claudenv.domain.rag_chunker.fallback_chunker import FallbackChunker
from claudenv.domain.rag_chunker.markdown_chunk_config import MarkdownChunkConfig
from claudenv.domain.rag_chunker.markdown_chunker import MarkdownChunker
from claudenv.domain.rag_chunker.tree_sitter_chunker import TreeSitterChunker
from claudenv.domain.rag_chunker.chunker_factory import ChunkerFactory
from claudenv.domain.rag_chunker.chunking_config import ChunkingConfig
from claudenv.domain.rag_chunker.make_chunker_factory import make_chunker_factory

__all__ = [
    "Chunk",
    "ChunkId",
    "ChunkType",
    "RepoSlug",
    "BranchName",
    "ContentHash",
    "Tier",
    "IChunker",
    "WindowConfig",
    "sliding_window_chunks",
    "FallbackChunker",
    "MarkdownChunkConfig",
    "MarkdownChunker",
    "TreeSitterChunker",
    "ChunkerFactory",
    "ChunkingConfig",
    "make_chunker_factory",
]
