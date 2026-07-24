"""
claude-env :: Ports - Configuration provider interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.rag import RAGConfig
from claudenv.ports.config.value_objects import EmbeddingConfig, RerankerConfig


class IConfigProvider(Protocol):
    """Configuration provider (environment + YAML)."""

    @abstractmethod
    def get_embedding_config(self) -> EmbeddingConfig:
        ...

    @abstractmethod
    def get_reranker_config(self) -> RerankerConfig:
        ...

    @abstractmethod
    def get_rag_config(self) -> RAGConfig:
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


class IDatabaseConfig(Protocol):
    """Database connection configuration."""

    @abstractmethod
    def get_database_dsn(self) -> str:
        ...


class ILanceDBConfig(Protocol):
    """LanceDB vector store configuration."""

    @abstractmethod
    def get_lancedb_path(self) -> str:
        ...


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


class IGlobalPolicyConfig(Protocol):
    """Global policy configuration."""

    @abstractmethod
    def get_global_policy(self) -> dict[str, Any]:
        ...


class IRepoPolicyConfig(Protocol):
    """Repository policy template configuration."""

    @abstractmethod
    def get_repo_policy_template(self) -> dict[str, Any]:
        ...


class IMCPConfig(Protocol):
    """MCP servers configuration."""

    @abstractmethod
    def get_mcp_servers_config(self) -> dict[str, Any]:
        ...


class IClaudeEnvHome(Protocol):
    """claude-env home directory location."""

    @abstractmethod
    def get_claude_env_home(self) -> str:
        ...
