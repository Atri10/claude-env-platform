"""
claude-env :: Domain - RAG Chunking Strategies - make_chunker_factory
"""
from __future__ import annotations

from claudenv.domain.rag_chunker.window_config import WindowConfig
from claudenv.domain.rag_chunker.markdown_chunk_config import MarkdownChunkConfig
from claudenv.domain.rag_chunker.markdown_chunker import MarkdownChunker
from claudenv.domain.rag_chunker.tree_sitter_chunker import TreeSitterChunker
from claudenv.domain.rag_chunker.fallback_chunker import FallbackChunker
from claudenv.domain.rag_chunker.chunker_factory import ChunkerFactory
from claudenv.domain.rag_chunker.chunking_config import ChunkingConfig


def make_chunker_factory(config: ChunkingConfig | None = None) -> ChunkerFactory:
    """Factory function to create a ChunkerFactory with config."""
    cfg = config or ChunkingConfig()
    return ChunkerFactory(
        code_chunker=TreeSitterChunker(
            max_chunk_chars=cfg.target_chars,
            overlap_chars=cfg.overlap_chars,
            fallback_config=WindowConfig(
                target_chars=cfg.target_chars,
                overlap_chars=cfg.overlap_chars,
            ),
        ),
        markdown_chunker=MarkdownChunker(MarkdownChunkConfig(
            max_chunk_chars=cfg.target_chars,
            overlap_chars=cfg.overlap_chars,
        )),
        fallback_chunker=FallbackChunker(WindowConfig(
            target_chars=cfg.target_chars,
            overlap_chars=cfg.overlap_chars,
        )),
    )
