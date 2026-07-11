"""
claude-env :: embedder backend interface
File: rag/embeddings/base.py

Purpose:
    The contract every embedding backend must satisfy so rag/config.py's
    get_embedder() factory can swap backends (llama.cpp today, others later)
    without any caller (indexer, retriever, memory) knowing which one is active.

    Adding a new backend means: implement this interface, register it in
    rag/embeddings/registry.py. No factory or caller code changes.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class EmbedderBackend(ABC):
    dim: int            # output vector dimension
    model_name: str      # human-readable label, stored in rag_index_state.embed_model
    backend_name: str    # registry key that produced this instance, e.g. "llama_cpp"

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of source documents/chunks."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embed a single search query."""

    def info(self) -> dict:
        """Model identity for logging/audit — not a health check."""
        return {"backend": self.backend_name, "model_name": self.model_name, "dim": self.dim}
