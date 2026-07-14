"""
claude-env :: Application - Onboarding Service - NativeHooksStep
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


class NativeHooksStep(OnboardingStep):
    """Install native tool governance hooks."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_template:
            ctx.add_step("native_hooks", skipped=True)
            return False

        installer = Path(self.config.get_claude_env_home()) / "hooks" / "install_hooks.py"
        if not installer.exists():
            ctx.add_step("native_hooks", skipped=True)
            return False

        if not ctx.dry_run:
            result = subprocess.run(
                [sys.executable, str(installer), "--repo", ctx.repo_root],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                print(f"  WARNING: hook install failed: {result.stderr.strip()}")

        ctx.add_step("native_hooks")
        return True
