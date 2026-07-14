"""
claude-env :: Ports - Global policy configuration interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IGlobalPolicyConfig(Protocol):
    """Global policy configuration."""

    @abstractmethod
    def get_global_policy(self) -> dict[str, Any]:
        ...
