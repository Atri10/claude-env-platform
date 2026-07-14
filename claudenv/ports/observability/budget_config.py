"""
claude-env :: Ports - Budget configuration interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class IBudgetConfig(Protocol):
    """Budget configuration (config/budgets.yaml)."""

    @abstractmethod
    def get_warn_at(self) -> float:
        ...

    @abstractmethod
    def get_default_budget(self) -> float:
        ...

    @abstractmethod
    def get_repo_budgets(self) -> dict[str, float]:
        ...
