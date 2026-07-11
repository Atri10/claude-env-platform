"""
claude-env :: reranker backend interface
File: rag/rerankers/base.py

Purpose:
    The contract every reranker backend must satisfy so rag/config.py's
    get_reranker() factory can swap backends (ONNX cross-encoder today,
    others later) without any caller knowing which one is active.

    Adding a new backend means: implement this interface, register it in
    rag/rerankers/registry.py. No factory or caller code changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class RerankerBackend(ABC):
    ok: bool             # whether the backend loaded successfully
    model_name: str       # human-readable label
    backend_name: str     # registry key that produced this instance
    _load_error: str      # set when ok is False

    @abstractmethod
    def rerank(self, query: str, candidates: list[dict], top_n: int = 8,
               text_key: str = "text") -> list[dict]:
        """Re-rank candidates for query. Must return candidates[:top_n] (identity) if not ok."""

    def status(self) -> dict:
        """Health + identity for logging/audit."""
        return {
            "ok": self.ok, "backend": self.backend_name, "model_name": self.model_name,
            "error": self._load_error if not self.ok else None,
        }
