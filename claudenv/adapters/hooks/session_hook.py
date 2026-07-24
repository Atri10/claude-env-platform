"""
claude-env :: Adapters - Session Hook (SessionStart / SessionEnd)

Records session lifecycle to the tamper-evident audit ledger so a session's
existence is attributable even when no tool calls occur (a session that only
reads data would otherwise leave no trace).

Dispatches on ``payload["hook_event_name"]`` (matcher "*"), following the same
never-block / never-raise shape as audit_hook.py and session_metrics_hook.py:
the whole body is wrapped so a failure can never disrupt the user's session.
"""
from __future__ import annotations

import json
import logging
import sys
from typing import Any

from claudenv.adapters.logging import configure_logging
from claudenv.ports import IAgentAuditLogger

logger = logging.getLogger(__name__)


class SessionHook:
    """Claude Code SessionStart/SessionEnd lifecycle hook."""

    # Install metadata (consumed by HookInstaller).
    HOOK_MATCHER = "*"
    HOOK_TIMEOUT = 5

    @staticmethod
    def run(
        payload: dict[str, Any],
        audit: IAgentAuditLogger,
    ) -> int:
        """Record a SessionStart/SessionEnd event. Returns 0 always."""
        try:
            event = payload.get("hook_event_name", "")
            session_id = (
                payload.get("session_id")
                or payload.get("sessionId")
                or "unknown"
            )
            logger.info("session hook event=%s session=%s", event, session_id)
            if event == "SessionStart":
                audit.agent_action(
                    agent="session", action="session_start", target=session_id,
                )
            elif event == "SessionEnd":
                audit.agent_action(
                    agent="session", action="session_end", target=session_id,
                )
        except Exception:
            logger.exception("session hook failed; continuing")
        return 0

    @staticmethod
    def main() -> None:
        """CLI entry point for the SessionStart/SessionEnd hook."""
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = {}
        audit = _build_audit()
        sys.exit(SessionHook.run(payload, audit))


def _build_audit() -> IAgentAuditLogger:
    """Build audit logger for the CLI entry point (this process is its own Main)."""
    from claudenv.adapters.audit import SqliteAuditLogger
    from claudenv.adapters.config import get_config
    from claudenv.adapters.persistence import SQLiteDatabase
    from claudenv.domain.value_objects import SessionId, Tier

    config = get_config()
    db = SQLiteDatabase(config.get_database_dsn())
    return SqliteAuditLogger(
        db,
        SessionId.from_string("session-hook"),
        actor="session-hook",
        repo="session-hook",
        tier=Tier.INTERNAL,
    )


def main() -> None:
    """Module entry point: ``python -m claudenv.adapters.hooks.session_hook``."""
    configure_logging(console=False)
    SessionHook.main()


if __name__ == "__main__":
    main()
