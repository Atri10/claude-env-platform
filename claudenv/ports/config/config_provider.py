"""
claude-env :: Ports - Configuration provider interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.ports.config.embedding_config import EmbeddingConfig
from claudenv.ports.config.reranker_config import RerankerConfig
from claudenv.ports.config.rag_config import RagConfig


class IConfigProvider(Protocol):
    """Configuration provider (environment + YAML)."""

    @abstractmethod
    def get_embedding_config(self) -> EmbeddingConfig:
        ...

    @abstractmethod
    def get_reranker_config(self) -> RerankerConfig:
        ...

    @abstractmethod
    def get_rag_config(self) -> RagConfig:
        ...

    @abstractmethod
    def get_database_dsn(self) -> str:
        ...

    @abstractmethod
    def get_lancedb_path(self) -> str:
        ...

    @abstractmethod
    def get_claude_env_home(self) -> str:
        ...

    @abstractmethod
    def get_mcp_servers_config(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_global_policy(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_repo_policy_template(self) -> dict[str, Any]:
        ...
