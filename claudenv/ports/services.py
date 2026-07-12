"""
claude-env :: Ports - Service Registry Interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IServiceRegistry(Protocol):
    """Local service discovery (ports, PIDs)."""

    @abstractmethod
    def register(
        self,
        name: str,
        port: int,
        pid: int | None = None,
        extra: dict | None = None,
    ) -> dict[str, Any]:
        ...

    @abstractmethod
    def unregister(self, name: str) -> None:
        ...

    @abstractmethod
    def get(self, name: str) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def list_live(self) -> list[dict[str, Any]]:
        ...