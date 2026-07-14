"""
claude-env :: Domain - RAG Chunking Strategies - Factory

Groups: ChunkingConfig, ChunkerFactory, make_chunker_factory.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claudenv.domain.rag import BranchName, Chunk, RepoSlug, Tier
from claudenv.domain.rag_chunker.chunkers import (
    FallbackChunker,
    IChunker,
    MarkdownChunkConfig,
    WindowConfig,
)
from claudenv.domain.rag_chunker.markdown_chunker import MarkdownChunker
from claudenv.domain.rag_chunker.tree_sitter_chunker import TreeSitterChunker


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


class ChunkerFactory:
    """Factory for selecting appropriate chunker by file extension."""

    def __init__(
            self,
            code_chunker: TreeSitterChunker | None = None,
            markdown_chunker: MarkdownChunker | None = None,
            fallback_chunker: FallbackChunker | None = None,
    ):
        self._code = code_chunker or TreeSitterChunker()
        self._markdown = markdown_chunker or MarkdownChunker()
        self._fallback = fallback_chunker or FallbackChunker()

        # Build extension -> chunker mapping
        self._map: dict[str, IChunker] = {}
        for ext in self._code.supported_extensions:
            self._map[ext] = self._code
        for ext in self._markdown.supported_extensions:
            self._map[ext] = self._markdown

    def get_chunker(self, file_path: str) -> IChunker:
        """Get appropriate chunker for file extension."""
        ext = Path(file_path).suffix.lower()
        return self._map.get(ext, self._fallback)

    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        """Convenience: get chunker and chunk in one call."""
        return self.get_chunker(file_path).chunk(file_path, text, repo, branch, commit, tier)


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
