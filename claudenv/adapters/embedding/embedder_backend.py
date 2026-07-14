"""
claude-env :: Adapters - Embedding - Backend base class
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EmbedderBackend(ABC):
    """Base class for embedding backends."""

    @property
    @abstractmethod
    def dim(self) -> int:
        ...

    @property
    @abstractmethod
    def model_name(self) -> str:
        ...

    @property
    @abstractmethod
    def backend_name(self) -> str:
        ...

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        ...

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        ...

    def info(self) -> dict[str, Any]:
        return {"backend": self.backend_name, "model_name": self.model_name, "dim": self.dim}
