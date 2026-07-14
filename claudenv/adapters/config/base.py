"""
claude-env :: Adapters - Configuration - Base loader
"""
from __future__ import annotations

import os
import yaml
from pathlib import Path


class _ConfigBase:
    """Base class with shared configuration loading logic."""

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

    def get_claude_env_home(self) -> str:
        return os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env"))
