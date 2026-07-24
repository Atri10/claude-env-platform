"""
claude-env :: Adapters - Configuration - Base loader
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from claudenv._data import config_dir

logger = logging.getLogger(__name__)


class _ConfigBase:
    """Base class with shared configuration loading logic."""

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or self._resolve_config_path()
        self._raw_config = self._load_config()

    def _resolve_config_path(self) -> Path:
        """Resolve config file path.

        Order: explicit env override -> deployed user copy under
        $CLAUDE_ENV_HOME/config/ (editable, authoritative) -> packaged default
        shipped in the wheel.
        """
        # Check env override
        if override := os.environ.get("RAG_CONFIG_YAML"):
            return Path(os.path.expanduser(override))

        # Deployed (user-editable) copy takes precedence.
        home = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
        deployed = home / "config" / "rag.yaml"
        if deployed.exists():
            return deployed

        # Fall back to the packaged default.
        return config_dir() / "rag.yaml"

    def _load_config(self) -> dict:
        if not self._config_path.exists():
            return {}
        try:
            return yaml.safe_load(self._config_path.read_text()) or {}
        except Exception:
            logger.error("failed to load config; using empty defaults", exc_info=True)
            return {}

    def _expand(self, value: str) -> str:
        return os.path.expanduser(os.path.expandvars(value)) if value else value

    def _get_env(self, key: str, default: str) -> str:
        return os.environ.get(key, self._expand(default))

    def get_claude_env_home(self) -> str:
        return os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env"))
