"""
claude-env :: Domain - RAG Chunking Strategies
Pure domain logic for chunking source files — no I/O, no external deps.
"""
from __future__ import annotations

# Re-exported so `from claudenv.domain.rag_chunker import X` keeps working
# exactly as it did when rag_chunker.py imported these from claudenv.domain.rag.
from claudenv.domain.rag import BranchName, Chunk, ChunkId, ChunkType, ContentHash, RepoSlug, Tier
from claudenv.domain.rag_chunker.chunkers import (
    FallbackChunker,
    IChunker,
    MarkdownChunkConfig,
    WindowConfig,
    sliding_window_chunks,
)
from claudenv.domain.rag_chunker.factory import ChunkerFactory, ChunkingConfig, make_chunker_factory
from claudenv.domain.rag_chunker.markdown_chunker import MarkdownChunker
from claudenv.domain.rag_chunker.tree_sitter_chunker import TreeSitterChunker

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
