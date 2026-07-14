"""
claude-env :: Adapters - Reranking - Backend base class
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class RerankerBackend(ABC):
    """Base class for reranker backends."""

    @abstractmethod
    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        """Return indices of top_k docs after reranking."""
        ...

    def status(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.__class__.__name__}
