"""
claude-env :: Adapters - Hook Interfaces - Hook Installer (abstract)

Consumer-owned interfaces for governance hooks.
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
