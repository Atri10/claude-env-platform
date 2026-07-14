"""
claude-env :: Ports - Policy repository interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IPolicyRepository(Protocol):
    """Repository for policy configurations."""

    @abstractmethod
    def get_global_policy(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def get_repo_policy(self, repo_root: str) -> dict[str, Any] | None:
        ...

    @abstractmethod
    def save_repo_policy(self, repo_root: str, policy: dict[str, Any]) -> None:
        ...
