"""
claude-env :: Adapters - Bootstrap Steps - Audit Init
"""
from __future__ import annotations

from claudenv.ports.bootstrap import BootstrapContext, BootstrapResult


class AuditInitStep:
    """Step 8: Initialize audit ledger with genesis event."""

    @property
    def name(self) -> str:
        return "audit-init"

    @property
    def description(self) -> str:
        return "Initialize audit ledger with genesis event"

    def execute(self, context: BootstrapContext) -> BootstrapResult:
        from claudenv.adapters.config import get_config
        from claudenv.adapters.persistence import SQLiteDatabase
        from claudenv.adapters.audit import SqliteAuditLogger
        from claudenv.domain.value_objects import SessionId

        config = get_config()
        dsn = context.dsn or config.get_database_dsn()
        db = SQLiteDatabase(dsn)
        log = SqliteAuditLogger(
            db=db,
            session_id=SessionId.from_string("bootstrap"),
            actor="bootstrap",
        )
        log.agent_action(agent="bootstrap", action="initialize",
                         summary="platform bootstrap complete", success=True)
        result = log.verify_chain()
        if result.ok:
            return BootstrapResult.ok("audit chain initialized and verified")
        else:
            return BootstrapResult.failure(f"audit chain verification failed at event {result.broken_at}")

    def can_skip(self, context: BootstrapContext) -> bool:
        return False
