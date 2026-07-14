"""
claude-env :: Ports - Reranker interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IReranker(Protocol):
    """Reranker for improving result ordering."""

    @abstractmethod
    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        ...

    def status(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.__class__.__name__}
