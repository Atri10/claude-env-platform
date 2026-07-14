"""
claude-env :: Ports - Configuration Interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any, Protocol

from claudenv.domain.rag import RAGConfig


@dataclass
class EmbeddingConfig:
    backend: str = "llama_cpp"
    model_path: str = ""
    model_name: str = ""
    n_ctx: int = 2048
    n_gpu_layers: int = -1
    embedding_dim: int = 768
    document_prefix: str = ""
    query_prefix: str = ""
    pooling_type: str = "mean"


@dataclass
class RerankerConfig:
    backend: str = "onnx_cross_encoder"
    model_dir: str = ""


@dataclass
class RagConfig:
    embedding_dim: int = 768
    embedding_backend: str = "llama_cpp"
    embedding_model_path: str = ""
    embedding_model_name: str = ""
    embedding_n_ctx: int = 2048
    embedding_n_gpu_layers: int = -1
    embedding_document_prefix: str = ""
    embedding_query_prefix: str = ""
    embedding_pooling_type: str = "mean"
    reranker_backend: str = "onnx_cross_encoder"
    reranker_model_dir: str = ""


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


# ============================================================================
# Focused Config Ports (SRP-compliant, consumer-owned by the config adapters)
# ============================================================================


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
