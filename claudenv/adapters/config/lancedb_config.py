"""
claude-env :: Adapters - Configuration - LanceDB
"""
from __future__ import annotations

import os
from pathlib import Path

from claudenv.ports import ILanceDBConfig

from .base import _ConfigBase


class LanceDBConfigProvider(_ConfigBase, ILanceDBConfig):
    """LanceDB vector store configuration provider."""

    def get_lancedb_path(self) -> str:
        return os.environ.get("LANCEDB_PATH", str(Path(self.get_claude_env_home()) / "knowledge" / "lancedb"))
