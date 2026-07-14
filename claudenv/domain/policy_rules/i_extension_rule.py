"""
claude-env :: Domain - Policy Rule Strategies - IExtensionRule
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IExtensionRule(ABC):
    """Interface for extension-matching rules."""

    @abstractmethod
    def matches(self, path: str) -> bool:
        ...

    @property
    @abstractmethod
    def extension(self) -> str:
        ...
