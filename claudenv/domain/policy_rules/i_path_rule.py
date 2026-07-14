"""
claude-env :: Domain - Policy Rule Strategies - IPathRule
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IPathRule(ABC):
    """Interface for path-matching rules."""

    @abstractmethod
    def matches(self, path: str) -> bool:
        ...

    @abstractmethod
    def describe(self) -> str:
        ...
