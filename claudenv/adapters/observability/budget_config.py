"""
claude-env :: Adapters - YAML budget config provider

Reads config/budgets.yaml, preferring the deployed copy at
$CLAUDE_ENV_HOME/config/budgets.yaml over the repo copy (platform convention:
config is authoritative under $CLAUDE_ENV_HOME). Coerces everything to float
defensively; a missing/empty file behaves like an all-defaults document.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import yaml

from claudenv._data import config_dir
from claudenv.ports.observability import IBudgetConfig

logger = logging.getLogger(__name__)


class YamlBudgetConfig(IBudgetConfig):
    """budgets.yaml-backed IBudgetConfig."""

    def __init__(self, config_path: Path | None = None):
        self._config_path = config_path or self._resolve_config_path()
        self._doc = self._load()

    @staticmethod
    def _claude_env_home() -> Path:
        return Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))

    def _resolve_config_path(self) -> Path:
        deployed = self._claude_env_home() / "config" / "budgets.yaml"
        if deployed.exists():
            return deployed
        return config_dir() / "budgets.yaml"

    def _load(self) -> dict:
        if not self._config_path.exists():
            return {}
        try:
            return yaml.safe_load(self._config_path.read_text()) or {}
        except Exception:
            logger.warning("failed to load budget config; using defaults", exc_info=True)
            return {}

    def get_warn_at(self) -> float:
        return float(self._doc.get("warn_at", 0.8))

    def _monthly(self) -> dict:
        return self._doc.get("monthly_usd") or {}

    def get_default_budget(self) -> float:
        return float(self._monthly().get("default", 0) or 0)

    def get_repo_budgets(self) -> dict[str, float]:
        repos = self._monthly().get("repos") or {}
        return {k: float(v) for k, v in repos.items()}
