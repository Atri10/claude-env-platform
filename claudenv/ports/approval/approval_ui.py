"""
claude-env :: Ports - Approval web UI interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class IApprovalUI(Protocol):
    """Approval web UI server."""

    @abstractmethod
    def start(self, port: int | None = None) -> str:
        ...

    @abstractmethod
    def stop(self) -> None:
        ...
