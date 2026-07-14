"""
claude-env :: Adapters - Bootstrap Steps - Environment Check
"""
from __future__ import annotations

import platform
import shutil
import sys

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class EnvironmentCheckStep:
    """Step 1: Check system environment (OS, Python, Git, Homebrew)."""

    @property
    def name(self) -> str:
        return "environment-check"

    @property
    def description(self) -> str:
        return "Verify platform, Python version, and required tools"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        warnings = []
        good = True

        sysname = platform.system()
        arch = platform.machine()
        if sysname == "Darwin" and arch == "arm64":
            pass  # OK
        elif sysname == "Linux":
            pass  # OK
        else:
            warnings.append(f"platform is {sysname}/{arch}; primary targets are macOS arm64 and Linux")
            good = False

        if sys.version_info >= (3, 13):
            pass  # OK
        else:
            warnings.append(f"python {platform.python_version()} < 3.13 required")
            good = False

        if not shutil.which("git"):
            warnings.append("git not found on PATH")
            good = False

        if not shutil.which("brew"):
            warnings.append("homebrew not found (needed for --with-brew)")

        message = "environment check passed" if good else "environment check has warnings"
        return BootstrapResult.ok(message, warnings) if good else BootstrapResult.failure(message, warnings)

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Always run environment check
