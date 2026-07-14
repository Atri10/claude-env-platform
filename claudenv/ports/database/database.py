"""
claude-env :: Ports - Database connection interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.ports.database.transaction import ITransaction


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
