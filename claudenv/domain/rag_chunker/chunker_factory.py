"""
claude-env :: Domain - RAG Chunking Strategies - ChunkerFactory

Strategy selection by file extension.
"""
from __future__ import annotations

from pathlib import Path

from claudenv.domain.rag import Chunk, RepoSlug, BranchName, Tier
from claudenv.domain.rag_chunker.i_chunker import IChunker
from claudenv.domain.rag_chunker.tree_sitter_chunker import TreeSitterChunker
from claudenv.domain.rag_chunker.markdown_chunker import MarkdownChunker
from claudenv.domain.rag_chunker.fallback_chunker import FallbackChunker


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
