"""
claude-env :: Application - Onboarding Service - AgentsStep
"""
from __future__ import annotations

import shutil
from pathlib import Path

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


class AgentsStep(OnboardingStep):
    """Install specialist agents."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_template:
            ctx.add_step("agents", skipped=True)
            return False

        src_dir = Path(self.config.get_claude_env_home()) / "templates" / "repo-onboarding" / ".claude" / "agents"
        dst_dir = Path(ctx.repo_root) / ".claude" / "agents"
        if not src_dir.exists():
            ctx.add_step("agents", skipped=True)
            return False

        agent_files = list(src_dir.glob("*.md"))
        if not agent_files:
            ctx.add_step("agents", skipped=True)
            return False

        installed = 0
        for src in sorted(agent_files):
            dst = dst_dir / src.name
            if dst.exists():
                continue
            if not ctx.dry_run:
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
            installed += 1

        if installed:
            print(f"  Installed {installed} specialist agents")
        ctx.add_step("agents")
        return True
