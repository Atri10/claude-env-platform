"""
claude-env :: Adapters - Bootstrap Steps - Venv Creation
"""
from __future__ import annotations

import subprocess
import venv as _venv
from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class VenvCreationStep:
    """Step 3: Create Python virtual environment."""

    @property
    def name(self) -> str:
        return "venv-creation"

    @property
    def description(self) -> str:
        return "Create Python virtual environment (prefers uv)"

    def __init__(self, uv_available: bool):
        self._uv_available = uv_available

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        venv_path = Path(context.venv_path)
        venv_py = Path(context.venv_python)

        if venv_py.exists() and not context.recreate_venv:
            return BootstrapResult.ok(f"venv already exists at {venv_path}")

        print(f"creating venv at {venv_path} ...")
        if self._uv_available:
            result = subprocess.run(
                ["uv", "venv", str(venv_path), "--python", "3.13"],
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                print(f"uv venv failed: {result.stderr}; falling back to stdlib venv")
                _venv.create(str(venv_path), with_pip=True, clear=context.recreate_venv, symlinks=True)
        else:
            _venv.create(str(venv_path), with_pip=True, clear=context.recreate_venv, symlinks=True)

        return BootstrapResult.ok(f"venv created: {venv_py}")

    def can_skip(self, context: BootstrapContext) -> bool:
        if context.no_venv_create:
            return True
        venv_py = Path(context.venv_python)
        return venv_py.exists() and not context.recreate_venv
