"""
claude-env :: Adapters - Bootstrap Step Factory
"""
from __future__ import annotations

from claudenv.ports.bootstrap import IBootstrapStep

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
