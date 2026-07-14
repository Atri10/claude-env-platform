"""
claude-env :: Domain - Policy Rule Strategies - IContentRule
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IContentRule(ABC):
    """Interface for content-scanning rules."""

    @abstractmethod
    def findall(self, text: str) -> list[str]:
        ...

    @abstractmethod
    def sub(self, text: str, replacement: str) -> str:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...
