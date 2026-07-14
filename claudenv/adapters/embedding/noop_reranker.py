"""
claude-env :: Adapters - Reranking - No-op backend (disabled)
"""
from __future__ import annotations

from typing import Any

from .reranker_backend import RerankerBackend


class NoopReranker(RerankerBackend):
    """No-op reranker (disabled)."""

    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        return list(range(min(top_k, len(docs))))

    def status(self) -> dict[str, Any]:
        return {"ok": False, "backend": "disabled"}
