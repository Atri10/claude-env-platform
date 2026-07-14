"""
claude-env :: Adapters - Bootstrap Steps - Database Init
"""
from __future__ import annotations

from pathlib import Path

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class DatabaseInitStep:
    """Step 5: Initialize SQLite database and apply schema."""

    @property
    def name(self) -> str:
        return "database-init"

    @property
    def description(self) -> str:
        return "Initialize SQLite database and apply migrations"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        # Import here to avoid circular imports
        from claudenv.adapters.config import get_config
        from claudenv.adapters.persistence import SQLiteDatabase

        config = get_config()
        dsn = context.dsn or config.get_database_dsn()
        db = SQLiteDatabase(dsn)

        repo_dir = Path(context.repo_dir)
        db.apply_schema(
            str(repo_dir / "sql" / "001_schema.sql"),
            str(repo_dir / "sql" / "002_retention.sql"),
            str(repo_dir / "sql" / "003_extensions.sql"),
            str(repo_dir / "sql" / "004_audit_trace_metadata.sql"),
        )
        return BootstrapResult.ok(f"database schema applied ({db.backend})")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False  # Idempotent, always run
