"""
claude-env :: Adapters - Configuration Provider
"""
from __future__ import annotations

import json
import os
import yaml
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claudenv.domain.rag import RAGConfig
from claudenv.ports import IConfigProvider


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


class ConfigProvider(IConfigProvider):
    """Configuration provider reading from YAML + environment."""

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or self._resolve_config_path()
        self._raw_config = self._load_config()

    def _resolve_config_path(self) -> Path:
        """Resolve config file path."""
        # Check env override
        if override := os.environ.get("RAG_CONFIG_YAML"):
            return Path(os.path.expanduser(override))

        # Check relative to this file
        relative = Path(__file__).resolve().parents[3] / "config" / "rag.yaml"
        if relative.exists():
            return relative

        # Check deployed location
        home = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
        deployed = home / "config" / "rag.yaml"
        if deployed.exists():
            return deployed

        return relative

    def _load_config(self) -> dict:
        if not self._config_path.exists():
            return {}
        try:
            return yaml.safe_load(self._config_path.read_text()) or {}
        except Exception:
            return {}

    def _expand(self, value: str) -> str:
        return os.path.expanduser(os.path.expandvars(value)) if value else value

    def _get_env(self, key: str, default: str) -> str:
        return os.environ.get(key, self._expand(default))

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

    def get_database_dsn(self) -> str:
        # Every other getter here derives its path from get_claude_env_home()
        # (itself CLAUDE_ENV_HOME-aware), but this one hardcoded Path.home() /
        # ".claude-env", silently ignoring CLAUDE_ENV_HOME. That meant pointing
        # CLAUDE_ENV_HOME at an isolated/test directory still wrote audit/
        # memory/rag state into the real deployed database.
        return os.environ.get(
            "CLAUDE_ENV_DSN",
            f"sqlite:///{self.get_claude_env_home()}/state/claude-env.db",
        )

    def get_lancedb_path(self) -> str:
        return os.environ.get("LANCEDB_PATH", str(Path(self.get_claude_env_home()) / "knowledge" / "lancedb"))

    def get_claude_env_home(self) -> str:
        return os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env"))

    def get_mcp_servers_config(self) -> dict[str, Any]:
        config_path = Path(self.get_claude_env_home()) / "config" / "mcp-servers.json"
        if config_path.exists():
            return json.loads(config_path.read_text())
        return {}

    def get_global_policy(self) -> dict[str, Any]:
        config_path = Path(self.get_claude_env_home()) / "config" / "global-policy.yaml"
        if config_path.exists():
            return yaml.safe_load(config_path.read_text()) or {}
        return {}

    def get_repo_policy_template(self) -> dict[str, Any]:
        config_path = Path(self.get_claude_env_home()) / "config" / "repo-policy.template.yaml"
        if config_path.exists():
            return yaml.safe_load(config_path.read_text()) or {}
        return {}


# Module-level singleton
_config_provider: ConfigProvider | None = None


def get_config(reload: bool = False) -> ConfigProvider:
    global _config_provider
    if _config_provider is None or reload:
        _config_provider = ConfigProvider()
    return _config_provider
