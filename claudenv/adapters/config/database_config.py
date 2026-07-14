"""
claude-env :: Adapters - Configuration - Database
"""
from __future__ import annotations

import os

from claudenv.ports import IDatabaseConfig

from .base import _ConfigBase


class DatabaseConfigProvider(_ConfigBase, IDatabaseConfig):
    """Database connection configuration provider."""

    def get_database_dsn(self) -> str:
        return os.environ.get(
            "CLAUDE_ENV_DSN",
            f"sqlite:///{self.get_claude_env_home()}/state/claude-env.db",
        )
