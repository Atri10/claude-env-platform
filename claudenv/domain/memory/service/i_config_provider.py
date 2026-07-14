"""
claude-env :: Domain - Memory Service (Domain Layer) - IConfigProvider
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class IConfigProvider(Protocol):
    """Configuration provider."""

    @abstractmethod
    def get_database_dsn(self) -> str:
        ...
