"""
claude-env :: Ports - Bootstrap step interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.ports.bootstrap.bootstrap_context import BootstrapContext
from claudenv.ports.bootstrap.bootstrap_result import BootstrapResult


class IBootstrapStep(Protocol):
    """Interface for a bootstrap step (OCP: open for extension, closed for modification)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique step identifier."""
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable description."""
        ...

    @abstractmethod
    def can_skip(self, context: BootstrapContext) -> bool:
        """Check if step can be skipped given the context."""
        ...

    @abstractmethod
    def execute(self, context: BootstrapContext) -> BootstrapResult:
        """Execute the step."""
        ...
