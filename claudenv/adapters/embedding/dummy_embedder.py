"""
claude-env :: Adapters - Embedding - Dummy backend (testing)
"""
from __future__ import annotations

from .embedder_backend import EmbedderBackend


class DummyEmbedder(EmbedderBackend):
    """Dummy embedder for testing (returns zero vectors)."""

    def __init__(self, dim: int = 768):
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_name(self) -> str:
        return "dummy"

    @property
    def backend_name(self) -> str:
        return "dummy"

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self._dim for _ in texts]

    def embed_query(self, text: str) -> list[float]:
        return [0.0] * self._dim
