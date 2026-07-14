"""
claude-env :: Application - Onboarding Service - CommandsStep
"""
from __future__ import annotations

import json
from pathlib import Path

from claudenv.application.onboarding.context import OnboardingContext
from claudenv.application.onboarding.step import OnboardingStep


class CommandsStep(OnboardingStep):
    """Create .claude/commands.json for terminal MCP server."""

    def execute(self, ctx: OnboardingContext) -> bool:
        dst = Path(ctx.repo_root) / ".claude" / "commands.json"
        if dst.exists():
            ctx.add_step("commands", skipped=True)
            return False

        # Detect test command
        r = Path(ctx.repo_root)
        test_cmd = None
        if (r / "go.mod").exists():
            test_cmd = "go test ./..."
        elif (r / "Cargo.toml").exists():
            test_cmd = "cargo test"
        elif (r / "package.json").exists():
            test_cmd = "npm test"
        elif any((r / f).exists() for f in ("pyproject.toml", "setup.py", "pytest.ini", "tox.ini")):
            test_cmd = "pytest -q"

        scaffold = {
            "_comment": "Commands for terminal.run_tests / run_benchmarks / run_audit",
            "run_tests": test_cmd or "",
            "run_benchmarks": "",
            "run_audit": "",
        }

        if not ctx.dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_text(json.dumps(scaffold, indent=2) + "\n")

        ctx.add_step("commands")
        return True
