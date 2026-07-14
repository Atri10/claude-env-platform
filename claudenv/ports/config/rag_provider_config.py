"""
claude-env :: Ports - RAG pipeline configuration interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.rag import RAGConfig

from claudenv.ports.config.embedding_config import EmbeddingConfig
from claudenv.ports.config.reranker_config import RerankerConfig


class IRAGConfig(Protocol):
    """RAG pipeline configuration."""

    @abstractmethod
    def get_embedding_config(self) -> EmbeddingConfig:
        ...

    @abstractmethod
    def get_reranker_config(self) -> RerankerConfig:
        ...

    @abstractmethod
    def get_rag_config(self) -> RAGConfig:
        ...
