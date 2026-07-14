"""
claude-env :: Adapters - Configuration - Repo Policy Template
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from claudenv.ports import IRepoPolicyConfig

from .base import _ConfigBase


class RepoPolicyConfigProvider(_ConfigBase, IRepoPolicyConfig):
    """Repository policy template configuration provider."""

    def get_repo_policy_template(self) -> dict[str, Any]:
        config_path = Path(self.get_claude_env_home()) / "config" / "repo-policy.template.yaml"
        if config_path.exists():
            return yaml.safe_load(config_path.read_text()) or {}
        return {}
