"""
claude-env :: Ports - Bootstrap orchestrator interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.ports.bootstrap.bootstrap_context import BootstrapContext
from claudenv.ports.bootstrap.bootstrap_result import BootstrapResult
from claudenv.ports.bootstrap.bootstrap_step import IBootstrapStep


class IBootstrapOrchestrator(Protocol):
    """Interface for bootstrap orchestrator."""

    @abstractmethod
    def add_step(self, step: IBootstrapStep) -> None:
        """Add a bootstrap step to the pipeline."""
        ...

    @abstractmethod
    def execute(self, context: BootstrapContext) -> list[BootstrapResult]:
        """Execute all steps in order."""
        ...

    @abstractmethod
    def get_step_names(self) -> list[str]:
        """Get names of all registered steps."""
        ...
