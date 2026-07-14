"""
claude-env :: Adapters - Bootstrap Steps - LanceDB Init
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class LanceDBInitStep:
    """Step 6: Initialize LanceDB vector store."""

    @property
    def name(self) -> str:
        return "lancedb-init"

    @property
    def description(self) -> str:
        return "Initialize LanceDB vector database"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        path = Path(context.home) / "knowledge" / "lancedb"
        path.mkdir(parents=True, exist_ok=True)

        # Test lancedb import in the venv
        result = subprocess.run(
            [context.venv_python, "-c", "import lancedb; lancedb.connect('.')"],
            capture_output=True,
            cwd=str(path),
        )
        if result.returncode == 0:
            return BootstrapResult.ok(f"lancedb initialized at {path}")
        else:
            warnings = ["lancedb not importable in venv; directory created, check pip output above"]
            return BootstrapResult.ok(f"lancedb directory created at {path}", warnings)

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent
