"""
claude-env :: Application - Onboarding Service - GitHooksStep
"""
from __future__ import annotations

import shutil
from pathlib import Path

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


class GitHooksStep(OnboardingStep):
    """Install git hooks for auto-reindex."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.no_post_commit:
            ctx.add_step("git_hooks", skipped=True)
            return False

        git_dir = Path(ctx.repo_root) / ".git"
        if not git_dir.exists() or git_dir.is_file():
            ctx.add_step("git_hooks", skipped=True)
            return False

        hook_names = ("post-commit", "post-merge", "post-checkout")
        hook_src_dir = Path(self.config.get_claude_env_home()) / "scripts"
        installed = []

        for name in hook_names:
            src = hook_src_dir / name
            if not src.exists():
                continue
            dst = git_dir / "hooks" / name
            if dst.exists():
                existing = dst.read_text(errors="ignore")
                if "claude-env" not in existing:
                    installed.append(f"{name} (kept existing)")
                    continue
            if not ctx.dry_run:
                shutil.copy2(src, dst)
                dst.chmod(0o755)
            installed.append(name)

        if installed:
            print(f"  Installed hooks: {', '.join(installed)}")
        else:
            print("  All hooks already installed")

        ctx.add_step("git_hooks")
        return True
