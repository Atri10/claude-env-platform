"""
claude-env :: Domain - Policy Rule Strategies - Interfaces

Groups: IContentRule, IExtensionRule, IPathRule.
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


class IExtensionRule(ABC):
    """Interface for extension-matching rules."""

    @abstractmethod
    def matches(self, path: str) -> bool:
        ...

    @property
    @abstractmethod
    def extension(self) -> str:
        ...


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
