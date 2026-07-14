"""
claude-env :: Ports - Repository policy template configuration interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IRepoPolicyConfig(Protocol):
    """Repository policy template configuration."""

    @abstractmethod
    def get_repo_policy_template(self) -> dict[str, Any]:
        ...
