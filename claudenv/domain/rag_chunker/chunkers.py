"""
claude-env :: Domain - RAG Chunking Strategies - Chunkers

Groups the small chunking building blocks: IChunker (interface),
WindowConfig, sliding_window_chunks, FallbackChunker, MarkdownChunkConfig.

MarkdownChunker and TreeSitterChunker stay in their own modules — they are
substantial strategies, not tiny value objects.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from claudenv.domain.rag import Chunk, ChunkId, ChunkType, RepoSlug, BranchName, ContentHash, Tier


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


@dataclass(frozen=True, slots=True)
class WindowConfig:
    """Configuration for sliding window chunking."""
    target_chars: int = 2000  # ~500 tokens
    overlap_chars: int = 200  # ~50 tokens overlap
    min_chars: int = 100


@dataclass(frozen=True, slots=True)
class MarkdownChunkConfig:
    """Configuration for markdown chunking."""
    max_chunk_chars: int = 3000
    overlap_chars: int = 200
    min_header_level: int = 1
    max_header_level: int = 3


def sliding_window_chunks(
        text: str,
        config: WindowConfig,
) -> list[tuple[int, int, str]]:
    """Split text into overlapping windows sized by character count.

    Returns list of (start_line, end_line, chunk_text) using 1-based line numbers.

    `target_chars`/`overlap_chars` are character budgets, not line counts: lines
    are accumulated into the current window until its character length reaches
    `target_chars`, then the window is flushed and a char-budget's worth of
    trailing lines are kept as the overlap for the next window.
    """
    lines = text.splitlines()
    if not lines:
        return []

    chunks: list[tuple[int, int, str]] = []
    buf: list[str] = []
    buf_chars = 0
    start = 0

    def flush(end_idx: int) -> None:
        chunk_text = "\n".join(buf)
        if len(chunk_text) >= config.min_chars or end_idx == len(lines):
            chunks.append((start + 1, end_idx, chunk_text))

    for i, line in enumerate(lines):
        buf.append(line)
        buf_chars += len(line) + 1  # +1 for the joining newline

        if buf_chars >= config.target_chars:
            flush(i + 1)

            # Keep enough trailing lines to cover the configured overlap.
            keep_chars = 0
            keep_count = 0
            for ln in reversed(buf):
                if keep_chars >= config.overlap_chars:
                    break
                keep_chars += len(ln) + 1
                keep_count += 1
            # Always drop at least one line so the window makes forward progress.
            keep_count = min(keep_count, len(buf) - 1) if len(buf) > 1 else 0

            start = i + 1 - keep_count
            buf = buf[len(buf) - keep_count:] if keep_count else []
            buf_chars = sum(len(ln) + 1 for ln in buf)

    if buf:
        flush(len(lines))

    return chunks


class FallbackChunker(IChunker):
    """Sliding window chunker for arbitrary text files (fallback for unknown types)."""

    def __init__(self, config: WindowConfig | None = None):
        self._config = config or WindowConfig()

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return ()  # fallback handles anything not matched

    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        ext = Path(file_path).suffix.lower().lstrip(".")
        file_type = ext or "text"

        windows = sliding_window_chunks(text, self._config)
        chunks = []
        for i, (start_line, end_line, chunk_text) in enumerate(windows):
            chunk = Chunk(
                chunk_id=ChunkId.from_parts(repo, file_path, start_line, end_line),
                repo=repo,
                branch=branch,
                commit_sha=commit,
                file_path=file_path,
                file_type=file_type,
                symbol_type=ChunkType.WINDOW,
                symbol_name=f"chunk_{i}",
                start_line=start_line,
                end_line=end_line,
                text=chunk_text,
                content_hash=ContentHash.compute(chunk_text),
                tier=tier,
                metadata={"chunker": "fallback", "window_index": i},
            )
            chunks.append(chunk)
        return chunks
