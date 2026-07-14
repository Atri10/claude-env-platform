"""
claude-env :: Domain - RAG Chunking Strategies - sliding_window_chunks

Helper: Sliding Window (used by fallback and as fallback for code)
"""
from __future__ import annotations

from claudenv.domain.rag_chunker.window_config import WindowConfig


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
