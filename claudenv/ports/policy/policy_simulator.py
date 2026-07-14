"""
claude-env :: Ports - Policy simulator interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IPolicySimulator(Protocol):
    """Policy simulation for dry-run testing."""

    @abstractmethod
    def simulate(self, repo_root: str, candidate_policy: dict) -> dict[str, Any]:
        ...

    @abstractmethod
    def diff(self, repo_root: str, candidate_policy: dict) -> dict[str, Any]:
        ...
