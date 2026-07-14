"""
claude-env :: Adapters - Configuration - Global Policy
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from claudenv.ports import IGlobalPolicyConfig

from .base import _ConfigBase


class GlobalPolicyConfigProvider(_ConfigBase, IGlobalPolicyConfig):
    """Global policy configuration provider."""

    def get_global_policy(self) -> dict[str, Any]:
        config_path = Path(self.get_claude_env_home()) / "config" / "global-policy.yaml"
        if config_path.exists():
            return yaml.safe_load(config_path.read_text()) or {}
        return {}
