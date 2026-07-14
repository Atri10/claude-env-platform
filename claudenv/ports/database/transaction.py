"""
claude-env :: Ports - Database transaction interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class ITransaction(Protocol):
    """Database transaction context manager."""

    @abstractmethod
    def __enter__(self) -> ITransaction:
        ...

    @abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        ...

    @abstractmethod
    def execute(self, sql: str, params: tuple = ()) -> int:
        ...

    @abstractmethod
    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def commit(self) -> None:
        ...

    @abstractmethod
    def rollback(self) -> None:
        ...
