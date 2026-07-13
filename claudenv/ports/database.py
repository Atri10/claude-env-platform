"""
claude-env :: Ports - Database Interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IDatabase(Protocol):
    """Database connection abstraction."""

    @abstractmethod
    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def execute(self, sql: str, params: tuple = ()) -> int:
        ...

    @abstractmethod
    def executemany(self, sql: str, params: list[tuple]) -> None:
        ...

    @abstractmethod
    def transaction(self) -> ITransaction:
        ...

    @abstractmethod
    def close(self) -> None:
        ...


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
