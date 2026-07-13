"""
claude-env :: Adapters - Bootstrap Orchestrator
"""
from __future__ import annotations

from typing import Protocol

from claudenv.ports.bootstrap import (
    BootstrapContext,
    BootstrapResult,
    IBootstrapOrchestrator,
    IBootstrapStep,
)


class BootstrapOrchestrator:
    """Orchestrates the bootstrap pipeline.

    Follows OCP: new steps can be added without modifying this class.
    Follows DIP: depends on IBootstrapStep abstraction, not concrete steps.
    """

    def __init__(self):
        self._steps: list[IBootstrapStep] = []

    def add_step(self, step: IBootstrapStep) -> "BootstrapOrchestrator":
        """Add a step to the pipeline. Returns self for chaining."""
        self._steps.append(step)
        return self

    def execute(self, context: BootstrapContext) -> list[BootstrapResult]:
        """Execute all steps in order, skipping those that can_skip."""
        results = []
        for step in self._steps:
            if step.can_skip(context):
                results.append(BootstrapResult.ok(f"{step.name}: skipped"))
                continue

            print(f"\n== {step.name} ==")
            result = step.execute(context)
            results.append(result)

            if result.success:
                print(f"[ok] {result.message}")
                for w in result.warnings:
                    print(f"[warn] {w}")
            else:
                print(f"[fail] {result.message}")
                for w in result.warnings:
                    print(f"[warn] {w}")
                # Continue pipeline but track failure
        return results

    def get_step_names(self) -> list[str]:
        return [step.name for step in self._steps]


class BootstrapStepFactory:
    """Factory for creating bootstrap steps with dependencies.

    Follows DIP: steps receive dependencies via constructor injection.
    """

    def __init__(self, uv_available: bool):
        self._uv_available = uv_available

    def create_steps(self) -> list[IBootstrapStep]:
        """Create all bootstrap steps in execution order."""
        return [
            EnvironmentCheckStep(),
            DirectoryLayoutStep(),
            VenvCreationStep(self._uv_available),
            DependencyInstallStep(self._uv_available),
            DatabaseInitStep(),
            LanceDBInitStep(),
            PolicyConfigStep(),
            AuditInitStep(),
            ValidationStep(),
            ActivationHintStep(),
        ]


# Import step classes here to avoid circular imports
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