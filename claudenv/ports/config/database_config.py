"""
claude-env :: Ports - Database connection configuration interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class IDatabaseConfig(Protocol):
    """Database connection configuration."""

    @abstractmethod
    def get_database_dsn(self) -> str:
        ...
