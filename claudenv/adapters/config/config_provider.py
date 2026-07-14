"""
claude-env :: Adapters - Configuration - Aggregate Provider
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from claudenv.domain.rag import RAGConfig
from claudenv.ports import IConfigProvider

from .base import _ConfigBase
from .database_config import DatabaseConfigProvider
from .lancedb_config import LanceDBConfigProvider
from .rag_config import RAGConfigProvider
from .global_policy_config import GlobalPolicyConfigProvider
from .repo_policy_config import RepoPolicyConfigProvider
from .mcp_config import MCPConfigProvider
from .models import EmbeddingConfig, RerankerConfig


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
