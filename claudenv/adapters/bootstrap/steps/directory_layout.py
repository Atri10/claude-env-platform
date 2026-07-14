"""
claude-env :: Adapters - Bootstrap Steps - Directory Layout
"""
from __future__ import annotations

from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class DirectoryLayoutStep:
    """Step 2: Create directory layout under $CLAUDE_ENV_HOME."""

    @property
    def name(self) -> str:
        return "directory-layout"

    @property
    def description(self) -> str:
        return "Create platform directory structure"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        dirs = [
            "state", "venv",
            "knowledge", "knowledge/lancedb", "knowledge/docs",
            "models",
            "archive", "archive/memory", "archive/audit", "archive/security",
            "logs", "config", "bin",
        ]
        home = Path(context.home)
        for d in dirs:
            (home / d).mkdir(parents=True, exist_ok=True)
        return BootstrapResult.ok(f"directory layout created under {home}")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent, always run
