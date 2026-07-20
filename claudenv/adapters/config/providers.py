"""
claude-env :: Adapters - Configuration Providers

Merges the previously separate database_config.py, lancedb_config.py,
rag_config.py, global_policy_config.py, repo_policy_config.py,
mcp_config.py, and config_provider.py modules into one themed module.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

from claudenv._data import config_dir
from claudenv.domain.rag import RAGConfig
from claudenv.ports import (
    IConfigProvider,
    IDatabaseConfig,
    IGlobalPolicyConfig,
    ILanceDBConfig,
    IMCPConfig,
    IRAGConfig,
    IRepoPolicyConfig,
)
from claudenv.ports.observability import ISessionMetricsRepository

from .base import _ConfigBase
from .models import EmbeddingConfig, RerankerConfig


def _resolve_config_file(name: str) -> Path:
    """Resolve a config file: deployed user copy first, packaged default second.

    The deployed copy under ``$CLAUDE_ENV_HOME/config/`` is authoritative and
    user-editable; the packaged default shipped in the wheel is the fallback.
    """
    home = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
    deployed = home / "config" / name
    if deployed.exists():
        return deployed
    return config_dir() / name


def write_rag_config(home: str | Path, data: dict) -> Path:
    """Write a rag.yaml mapping to the deployed config dir.

    Mirrors the loader's path convention (``$CLAUDE_ENV_HOME/config/rag.yaml``)
    and creates the config dir if missing. Used by ``claude-env model setup``
    and ``claude-env init --interactive`` to persist model choices.
    """
    home = Path(home)
    cfg_dir = home / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = cfg_dir / "rag.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False, default_flow_style=False))
    return path


class DatabaseConfigProvider(_ConfigBase, IDatabaseConfig):
    """Database connection configuration provider."""

    def get_database_dsn(self) -> str:
        return os.environ.get(
            "CLAUDE_ENV_DSN",
            f"sqlite:///{self.get_claude_env_home()}/state/claude-env.db",
        )


class LanceDBConfigProvider(_ConfigBase, ILanceDBConfig):
    """LanceDB vector store configuration provider."""

    def get_lancedb_path(self) -> str:
        return os.environ.get("LANCEDB_PATH", str(Path(self.get_claude_env_home()) / "knowledge" / "lancedb"))


class RAGConfigProvider(_ConfigBase, IRAGConfig):
    """RAG pipeline configuration provider."""

    def get_embedding_config(self) -> EmbeddingConfig:
        emb = self._raw_config.get("embedding", {})
        return EmbeddingConfig(
            backend=self._get_env("EMBED_BACKEND", emb.get("backend", "llama_cpp")),
            model_path=self._expand(self._get_env("EMBED_MODEL_PATH", emb.get("model_path", ""))),
            model_name=self._get_env("EMBED_MODEL_NAME", emb.get("model_name", "")),
            n_ctx=int(self._get_env("EMBED_CTX", str(emb.get("n_ctx", 2048)))),
            n_gpu_layers=int(self._get_env("EMBED_GPU_LAYERS", str(emb.get("n_gpu_layers", -1)))),
            embedding_dim=int(self._get_env("EMBED_DIM", str(emb.get("embedding_dim", 768)))),
            document_prefix=self._get_env("EMBED_DOC_PREFIX", emb.get("document_prefix", "")),
            query_prefix=self._get_env("EMBED_QUERY_PREFIX", emb.get("query_prefix", "")),
            pooling_type=self._get_env("EMBED_POOLING_TYPE", emb.get("pooling_type", "mean")),
        )

    def get_reranker_config(self) -> RerankerConfig:
        rer = self._raw_config.get("reranker", {})
        rer_dir = self._get_env("RERANKER_DIR", rer.get("model_dir", ""))
        return RerankerConfig(
            backend=self._get_env("RERANKER_BACKEND", rer.get("backend", "onnx_cross_encoder")),
            model_dir=self._expand(rer_dir) if rer_dir else "",
        )

    def get_rag_config(self) -> RAGConfig:
        emb = self.get_embedding_config()
        rer = self.get_reranker_config()
        return RAGConfig(
            embedding_dim=emb.embedding_dim,
            embedding_backend=emb.backend,
            embedding_model_path=emb.model_path,
            embedding_model_name=emb.model_name,
            embedding_n_ctx=emb.n_ctx,
            embedding_n_gpu_layers=emb.n_gpu_layers,
            embedding_document_prefix=emb.document_prefix,
            embedding_query_prefix=emb.query_prefix,
            embedding_pooling_type=emb.pooling_type,
            reranker_backend=rer.backend,
            reranker_model_dir=rer.model_dir,
        )


class GlobalPolicyConfigProvider(_ConfigBase, IGlobalPolicyConfig):
    """Global policy configuration provider."""

    def get_global_policy(self) -> dict[str, Any]:
        config_path = _resolve_config_file("global-policy.yaml")
        if config_path.exists():
            return yaml.safe_load(config_path.read_text()) or {}
        return {}


class RepoPolicyConfigProvider(_ConfigBase, IRepoPolicyConfig):
    """Repository policy template configuration provider."""

    def get_repo_policy_template(self) -> dict[str, Any]:
        config_path = _resolve_config_file("repo-policy.template.yaml")
        if config_path.exists():
            return yaml.safe_load(config_path.read_text()) or {}
        return {}


class MCPConfigProvider(_ConfigBase, IMCPConfig):
    """MCP servers configuration provider."""

    def get_mcp_servers_config(self) -> dict[str, Any]:
        config_path = _resolve_config_file("mcp-servers.json")
        if config_path.exists():
            return json.loads(config_path.read_text())
        return {}


class ConfigProvider(_ConfigBase, IConfigProvider):
    """Aggregate configuration provider implementing all focused interfaces.

    Uses composition over multiple inheritance to avoid MRO conflicts.
    """

    def __init__(self, config_path: Path | None = None):
        super().__init__(config_path)
        # Create sub-providers with the same config path
        self._db = DatabaseConfigProvider(config_path)
        self._lancedb = LanceDBConfigProvider(config_path)
        self._rag = RAGConfigProvider(config_path)
        self._global_policy = GlobalPolicyConfigProvider(config_path)
        self._repo_policy = RepoPolicyConfigProvider(config_path)
        self._mcp = MCPConfigProvider(config_path)

    # IDatabaseConfig
    def get_database_dsn(self) -> str:
        return self._db.get_database_dsn()

    def get_session_metrics_repository(self) -> "ISessionMetricsRepository":
        """Session cost-tracking writer backed by the shared SQLite DB."""
        from claudenv.adapters.observability.repositories import (
            SQLiteMetricsRepository,
        )
        from claudenv.adapters.persistence.sqlite.database import SQLiteDatabase

        return SQLiteMetricsRepository(SQLiteDatabase(self.get_database_dsn()))

    # ILanceDBConfig
    def get_lancedb_path(self) -> str:
        return self._lancedb.get_lancedb_path()

    # IRAGConfig
    def get_embedding_config(self) -> EmbeddingConfig:
        return self._rag.get_embedding_config()

    def get_reranker_config(self) -> RerankerConfig:
        return self._rag.get_reranker_config()

    def get_rag_config(self) -> RAGConfig:
        return self._rag.get_rag_config()

    # IGlobalPolicyConfig
    def get_global_policy(self) -> dict[str, Any]:
        return self._global_policy.get_global_policy()

    # IRepoPolicyConfig
    def get_repo_policy_template(self) -> dict[str, Any]:
        return self._repo_policy.get_repo_policy_template()

    # IMCPConfig
    def get_mcp_servers_config(self) -> dict[str, Any]:
        return self._mcp.get_mcp_servers_config()

    # IClaudeEnvHome
    def get_claude_env_home(self) -> str:
        return super().get_claude_env_home()


# Module-level singleton
_config_provider: ConfigProvider | None = None


def get_config() -> ConfigProvider:
    """Get the global config provider singleton."""
    global _config_provider
    if _config_provider is None:
        _config_provider = ConfigProvider()
    return _config_provider


# --- Focused provider singletons for DI registration ---

_db_config_provider: DatabaseConfigProvider | None = None
_lancedb_config_provider: LanceDBConfigProvider | None = None
_rag_config_provider: RAGConfigProvider | None = None
_global_policy_config_provider: GlobalPolicyConfigProvider | None = None
_repo_policy_config_provider: RepoPolicyConfigProvider | None = None
_mcp_config_provider: MCPConfigProvider | None = None
_claude_env_home_provider: _ConfigBase | None = None


def get_database_config() -> DatabaseConfigProvider:
    global _db_config_provider
    if _db_config_provider is None:
        _db_config_provider = DatabaseConfigProvider()
    return _db_config_provider


def get_lancedb_config() -> LanceDBConfigProvider:
    global _lancedb_config_provider
    if _lancedb_config_provider is None:
        _lancedb_config_provider = LanceDBConfigProvider()
    return _lancedb_config_provider


def get_rag_config_provider() -> RAGConfigProvider:
    global _rag_config_provider
    if _rag_config_provider is None:
        _rag_config_provider = RAGConfigProvider()
    return _rag_config_provider


def get_global_policy_config() -> GlobalPolicyConfigProvider:
    global _global_policy_config_provider
    if _global_policy_config_provider is None:
        _global_policy_config_provider = GlobalPolicyConfigProvider()
    return _global_policy_config_provider


def get_repo_policy_config() -> RepoPolicyConfigProvider:
    global _repo_policy_config_provider
    if _repo_policy_config_provider is None:
        _repo_policy_config_provider = RepoPolicyConfigProvider()
    return _repo_policy_config_provider


def get_mcp_config() -> MCPConfigProvider:
    global _mcp_config_provider
    if _mcp_config_provider is None:
        _mcp_config_provider = MCPConfigProvider()
    return _mcp_config_provider


def get_claude_env_home_provider() -> _ConfigBase:
    global _claude_env_home_provider
    if _claude_env_home_provider is None:
        _claude_env_home_provider = _ConfigBase()
    return _claude_env_home_provider
