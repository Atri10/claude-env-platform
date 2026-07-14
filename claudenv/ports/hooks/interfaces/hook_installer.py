"""
claude-env :: Ports - Hook installer interface
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class HookInstaller(ABC):
    """Abstract hook installer."""

    @abstractmethod
    def install(self, scope: str = "repo") -> dict:
        """Install hooks."""
        pass

    @abstractmethod
    def uninstall(self, scope: str = "repo") -> dict:
        """Uninstall hooks."""
        pass

    @abstractmethod
    def validate(self) -> dict:
        """Validate installation."""
        pass
