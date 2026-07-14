"""
claude-env :: Ports - LanceDB vector store configuration interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class ILanceDBConfig(Protocol):
    """LanceDB vector store configuration."""

    @abstractmethod
    def get_lancedb_path(self) -> str:
        ...
