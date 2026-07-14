"""
claude-env :: Adapters - SQLite projection repository

Read-only access to the audit projections the dashboard summarizes:
policy_violations, security_events, human_approvals.
"""
from __future__ import annotations

from typing import Any

from claudenv.ports.database import IDatabase
from claudenv.ports.observability import IProjectionRepository


class SQLiteProjectionRepository(IProjectionRepository):
    """IProjectionRepository backed by the shared SQLite database."""

    def __init__(self, db: IDatabase):
        self._db = db

    def recent_policy_violations(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT ts, COALESCE(repo,'(none)') AS repo, path, rule, decision, actor "
            "FROM policy_violations ORDER BY ts DESC LIMIT ?",
            (limit,),
        )

    def security_event_breakdown(self, since_iso: str) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT severity, category, COUNT(*) AS n FROM security_events "
            "WHERE ts>=? GROUP BY severity, category ORDER BY n DESC",
            (since_iso,),
        )

    def open_approvals(self) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT request_id, agent, COALESCE(repo,'(none)') AS repo, action, "
            "requested_at FROM human_approvals "
            "WHERE decision IS NULL OR decision='pending' "
            "ORDER BY requested_at DESC",
        )
