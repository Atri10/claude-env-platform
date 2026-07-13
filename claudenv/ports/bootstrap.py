"""
claude-env :: Ports - Bootstrap Interfaces

Consumer-owned interfaces for bootstrap steps.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class BootstrapContext:
    """Context passed to all bootstrap steps."""

    # Paths
    repo_dir: str
    home: str
    venv_path: str
    venv_python: str
    venv_pip: str

    # CLI flags
    with_brew: bool = False
    no_deps: bool = False
    no_venv_create: bool = False
    recreate_venv: bool = False
    force_config: bool = False
    dsn: str | None = None

    # Runtime info
    uv_available: bool = False

    # Runtime state
    env_ok: bool = True
    venv_created: bool = False
    deps_installed: bool = False


@dataclass
class BootstrapResult:
    """Result of a bootstrap step."""

    success: bool = False
    message: str = ""
    warnings: list[str] = field(default_factory=list)
    step_name: str = ""

    @classmethod
    def ok(cls, message: str, warnings: list[str] | None = None, step_name: str = "") -> BootstrapResult:
        return cls(success=True, message=message, warnings=warnings or [], step_name=step_name)

    @classmethod
    def failure(cls, message: str, warnings: list[str] | None = None, step_name: str = "") -> BootstrapResult:
        return cls(success=False, message=message, warnings=warnings or [], step_name=step_name)


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