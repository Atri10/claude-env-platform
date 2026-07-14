"""
claude-env :: Domain - Memory Service (Domain Layer) - IEmbeddingProvider
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class IEmbeddingProvider(Protocol):
    """Embedding model provider."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        ...

    @abstractmethod
    def dim(self) -> int:
        ...
