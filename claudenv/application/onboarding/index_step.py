"""
claude-env :: Application - Onboarding Service - IndexStep
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


class IndexStep(OnboardingStep):
    """Offer to build initial RAG index."""

    def execute(self, ctx: OnboardingContext) -> bool:
        if ctx.dry_run or not sys.stdin.isatty() or not sys.stdout.isatty():
            print(f"  Next: build the RAG index when ready -> claude-env index {ctx.repo_root}")
            ctx.add_step("index", skipped=True)
            return False

        try:
            ans = input("  Build the RAG index for this repo now? (y/N): ").strip().lower()
        except EOFError:
            ans = "n"

        if ans not in ("y", "yes"):
            print(f"  skipped — run {ctx.repo_root} later")
            ctx.add_step("index", skipped=True)
            return False

        script = Path(self.config.get_claude_env_home()) / "rag" / "bootstrap_rag.py"
        if not script.exists():
            print("  index script not found")
            ctx.add_step("index", skipped=True)
            return False

        env = {**os.environ, "CLAUDE_ENV_REPO_NAME": str(ctx.slug), "CLAUDE_ENV_BRANCH": str(ctx.branch)}
        print(f"  indexing {ctx.repo_root} …")
        rc = subprocess.run([sys.executable, str(script), ctx.repo_root], env=env).returncode
        print(f"  {'indexed' if rc == 0 else 'indexing reported errors (see above)'}")
        ctx.add_step("index")
        return True
