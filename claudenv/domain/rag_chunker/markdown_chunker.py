"""
claude-env :: Domain - RAG Chunking Strategies - MarkdownChunker

Header-hierarchy aware chunking for Markdown files.
"""
from __future__ import annotations

import re

from claudenv.domain.rag import Chunk, ChunkId, ChunkType, RepoSlug, BranchName, ContentHash, Tier
from claudenv.domain.rag_chunker.chunkers import (
    IChunker,
    MarkdownChunkConfig,
    WindowConfig,
    sliding_window_chunks,
)


class MarkdownChunker(IChunker):
    """Header-hierarchy aware chunking for Markdown files."""

    def __init__(self, config: MarkdownChunkConfig | None = None):
        self._config = config or MarkdownChunkConfig()

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".md", ".mdx", ".rst", ".markdown")

    def chunk(
            self,
            file_path: str,
            text: str,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            tier: Tier = Tier.INTERNAL,
    ) -> list[Chunk]:
        lines = text.splitlines()
        if not lines:
            return []

        # Parse markdown headers
        header_re = re.compile(r"^(#{1,6})\s+(.+)$")
        sections = []
        current_header = "preamble"
        current_level = 0
        section_start = 0

        for i, line in enumerate(lines):
            m = header_re.match(line)
            if m:
                level = len(m.group(1))
                title = m.group(2).strip()

                # Only split on headers within our range
                if self._config.min_header_level <= level <= self._config.max_header_level:
                    if i > section_start:
                        sections.append({
                            "header": current_header,
                            "level": current_level,
                            "start": section_start,
                            "end": i,
                        })
                    current_header = title[:80] or "section"
                    current_level = level
                    section_start = i

        # Add final section
        if section_start < len(lines):
            sections.append({
                "header": current_header,
                "level": current_level,
                "start": section_start,
                "end": len(lines),
            })

        # Build chunks from sections
        chunks = []
        for i, sec in enumerate(sections):
            sec_lines = lines[sec["start"]:sec["end"]]
            sec_text = "\n".join(sec_lines)

            # If section too large, split with sliding window
            if len(sec_text) > self._config.max_chunk_chars:
                windows = sliding_window_chunks(
                    sec_text,
                    WindowConfig(
                        target_chars=self._config.max_chunk_chars,
                        overlap_chars=self._config.overlap_chars,
                    ),
                )
                for w_idx, (ws, we, wt) in enumerate(windows):
                    # Adjust line numbers to file-relative
                    file_start = sec["start"] + ws
                    file_end = sec["start"] + we
                    chunks.append(self._make_chunk(
                        repo, branch, commit, file_path, tier, i,
                        sec["header"], ChunkType.SECTION, file_start, file_end, wt,
                        f"window_{w_idx}",
                    ))
            else:
                chunks.append(self._make_chunk(
                    repo, branch, commit, file_path, tier, i,
                    sec["header"], ChunkType.SECTION,
                    sec["start"] + 1, sec["end"], sec_text, "",
                ))

        return chunks

    def _make_chunk(
            self,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            file_path: str,
            tier: Tier,
            index: int,
            header: str,
            sym_type: ChunkType,
            start: int,
            end: int,
            text: str,
            suffix: str,
    ) -> Chunk:
        sym_name = f"{header[:60]}{('_' + suffix) if suffix else ''}"
        return Chunk(
            chunk_id=ChunkId.from_parts(repo, file_path, start, end),
            repo=repo,
            branch=branch,
            commit_sha=commit,
            file_path=file_path,
            file_type="markdown",
            symbol_type=sym_type,
            symbol_name=sym_name[:100],
            start_line=start,
            end_line=end,
            text=text,
            content_hash=ContentHash.compute(text),
            tier=tier,
            metadata={"chunker": "markdown", "header": header[:80]},
        )
