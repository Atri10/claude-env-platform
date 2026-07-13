"""
claude-env :: Application - Bootstrap Orchestrator

Orchestrates the bootstrap process using a pipeline of steps.
Each step is a single-responsibility component (SRP) implementing IBootstrapStep.
New steps can be added without modifying the orchestrator (OCP).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from claudenv.ports.bootstrap import IBootstrapStep, BootstrapContext, BootstrapResult


class IBootstrapStep(Protocol):
    """Interface for a bootstrap step (OCP: open for extension)."""

    @property
    def name(self) -> str:
        """Unique step identifier."""
        ...

    @property
    def description(self) -> str:
        """Human-readable description."""
        ...

    def can_skip(self, context: BootstrapContext) -> bool:
        """Check if step can be skipped given the context."""
        ...

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        """Execute the step."""
        ...


@dataclass
class BootstrapContext:
    """Context passed to all bootstrap steps (ISP: focused context)."""

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

    # Runtime state (mutated by steps)
    env_ok: bool = True
    venv_created: bool = False
    deps_installed: bool = False

    def __post_init__(self):
        # Ensure paths are absolute
        self.repo_dir = str(Path(self.repo_dir).resolve())
        self.home = str(Path(self.home).resolve())
        self.venv_path = str(Path(self.venv_path).resolve())
        self.venv_python = str(Path(self.venv_python).resolve())
        self.venv_pip = str(Path(self.venv_pip).resolve())


@dataclass
class BootstrapResult:
    """Result of a bootstrap step."""

    success: bool
    message: str
    warnings: list[str] = field(default_factory=list)
    step_name: str = ""

    @classmethod
    def success(cls, message: str, warnings: list[str] | None = None, step_name: str = "") -> BootstrapResult:
        return cls(success=True, message=message, warnings=warnings or [], step_name=step_name)

    @classmethod
    def failure(cls, message: str, warnings: list[str] | None = None, step_name: str = "") -> BootstrapResult:
        return cls(success=False, message=message, warnings=warnings or [], step_name=step_name)

    def is_fatal(self) -> bool:
        """Whether this failure should stop the bootstrap."""
        return not self.success


class BootstrapOrchestrator:
    """
    Orchestrates the bootstrap pipeline (SRP: only orchestrates steps).

    Follows DIP: depends on IBootstrapStep abstraction, not concrete steps.
    Steps are injected via constructor (dependency injection).
    """

    def __init__(self, steps: list[IBootstrapStep] | None = None):
        self._steps = steps or []

    def add_step(self, step: IBootstrapStep) -> None:
        """Add a bootstrap step to the pipeline (OCP: extend without modification)."""
        self._steps.append(step)

    def execute(self, context: BootstrapContext) -> list[BootstrapResult]:
        """Execute all steps in order, stopping on fatal failure."""
        results: list[BootstrapResult] = []

        for step in self._steps:
            if step.can_skip(context):
                results.append(BootstrapResult.success(
                    f"{step.name} skipped", step_name=step.name
                ))
                continue

            print(f"\n=== {step.description} ===")
            try:
                result = step.execute(context)
                result.step_name = step.name
                results.append(result)

                if result.success:
                    print(f"  ✓ {result.message}")
                    for warning in result.warnings:
                        print(f"  ⚠ {warning}")
                else:
                    print(f"  ✗ {result.message}")
                    for warning in result.warnings:
                        print(f"  ⚠ {warning}")
                    if result.is_fatal():
                        print(f"\nBootstrap stopped at step: {step.name}")
                        break

            except Exception as e:
                result = BootstrapResult.failure(
                    f"{step.name} raised exception: {e}", step_name=step.name
                )
                results.append(result)
                print(f"  ✗ {result.message}")
                break

        return results

    def get_step_names(self) -> list[str]:
        """Get names of all registered steps."""
        return [step.name for step in self._steps]


def create_default_orchestrator() -> BootstrapOrchestrator:
    """Create orchestrator with default bootstrap steps (Factory pattern)."""
    from claudenv.adapters.bootstrap.steps import (
        EnvironmentCheckStep,
        DirectoryLayoutStep,
        VenvCreationStep,
        DependencyInstallStep,
        DatabaseInitStep,
        LanceDBInitStep,
        PolicyConfigStep,
        AuditInitStep,
        ValidationStep,
        ActivationHintStep,
    )

    orchestrator = BootstrapOrchestrator()
    orchestrator.add_step(EnvironmentCheckStep())
    orchestrator.add_step(DirectoryLayoutStep())
    orchestrator.add_step(VenvCreationStep())
    orchestrator.add_step(DependencyInstallStep())
    orchestrator.add_step(DatabaseInitStep())
    orchestrator.add_step(LanceDBInitStep())
    orchestrator.add_step(PolicyConfigStep())
    orchestrator.add_step(AuditInitStep())
    orchestrator.add_step(ValidationStep())
    orchestrator.add_step(ActivationHintStep())
    return orchestrator