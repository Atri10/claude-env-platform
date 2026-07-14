"""
claude-env :: Adapters - Bootstrap Steps - Validation
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class ValidationStep:
    """Step 9: Run validation script via venv Python."""

    @property
    def name(self) -> str:
        return "validation"

    @property
    def description(self) -> str:
        return "Run installation validation using venv Python"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        script = Path(context.repo_dir) / "claudenv" / "validation" / "validate_installation.py"
        if not script.exists():
            return BootstrapResult.ok("validate_installation.py not found; skipping final validation",
                                           ["validate_installation.py not found; skipping final validation"])

        result = subprocess.run([context.venv_python, str(script)])
        if result.returncode == 0:
            return BootstrapResult.ok("installation validation passed")
        else:
            return BootstrapResult.failure("installation validation failed")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False
