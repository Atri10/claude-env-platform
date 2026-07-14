"""
claude-env :: Ports - Feedback repository interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IFeedbackRepository(Protocol):
    """Read/write access to rag_chunk_feedback."""

    @abstractmethod
    def record_retrieved(
        self,
        repo: str,
        branch: str,
        query_hash: str,
        chunks: list[dict[str, Any]],
        session_id: str,
    ) -> None:
        ...

    @abstractmethod
    def used_counts(self, repo: str, branch: str) -> dict[str, int]:
        ...

    @abstractmethod
    def signal_totals(self, repo: str | None = None) -> dict[str, int]:
        ...

    @abstractmethod
    def top_used_files(self, repo: str | None = None, limit: int = 10) -> list[tuple[str, int]]:
        ...
