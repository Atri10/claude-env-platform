"""
claude-env :: Domain - RAG Chunking Strategies - IChunker
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from claudenv.domain.rag import Chunk, RepoSlug, BranchName, Tier


class IChunker(ABC):
    """Protocol for chunking strategies — pure domain logic, no I/O."""

    @property
    @abstractmethod
    def supported_extensions(self) -> tuple[str, ...]:
        """File extensions this chunker handles (lowercase, with dot)."""
        ...

    @abstractmethod
    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        """Split file text into chunks with metadata."""
        ...
