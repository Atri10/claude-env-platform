"""
claude-env :: Domain - RAG Chunking Strategies - FallbackChunker

Sliding window for any text file.
"""
from __future__ import annotations

from pathlib import Path

from claudenv.domain.rag import Chunk, ChunkId, ChunkType, RepoSlug, BranchName, ContentHash, Tier
from claudenv.domain.rag_chunker.i_chunker import IChunker
from claudenv.domain.rag_chunker.window_config import WindowConfig
from claudenv.domain.rag_chunker.sliding_window_chunks import sliding_window_chunks


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
