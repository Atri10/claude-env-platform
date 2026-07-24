"""
claude-env :: Adapters - SQLite Persistence - Audit Repository

SQLite implementation of ``IAuditRepository``. Persists hash-chained audit
events; the domain layer owns canonical construction + hashing.
"""
from __future__ import annotations

import os
import socket
import uuid
from typing import Any

from claudenv.domain.audit import (
    AuditEvent,
    ChainVerificationResult,
    EventId,
    EventType,
    LedgerRow,
    SessionId,
    Tier,
    verify_ledger_chain,
)
from claudenv.domain.value_objects import RepoSlug
from claudenv.ports import IAuditRepository

from .database import SQLiteDatabase


class SQLiteAuditRepository(IAuditRepository):
    """SQLite audit event store with hash chain."""

    def __init__(self, db: SQLiteDatabase):
        self._db = db

    def append(
            self,
            event_type: EventType,
            payload: dict[str, Any],
            session_id: SessionId,
            actor: str,
            repo: RepoSlug | None,
            tier: Tier | None,
            projection: tuple[str, dict[str, Any]] | None = None,
    ) -> EventId:
        with self._db.transaction() as tx:
            prev_row = tx.query(
                "SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1"
            )
            prev_hash = prev_row[0]["event_hash"] if prev_row else "GENESIS"

            # Domain owns canonical construction + hashing (single source of
            # truth shared with SqliteAuditLogger); this adapter only persists
            # the result and supplies process-identity trace metadata.
            event = AuditEvent.create(
                event_type=event_type,
                actor=actor,
                session_id=session_id,
                payload=payload,
                repo=repo,
                tier=tier,
                prev_hash=prev_hash,
                host=socket.gethostname(),
                pid=os.getpid(),
                request_id=f"req-{uuid.uuid4().hex[:16]}",
            )

            tx.execute(
                "INSERT INTO audit_events "
                "(ts, event_type, actor, session_id, repo, tier, payload_json, prev_hash, "
                " event_hash, schema_version, host, pid, request_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event.ts, event_type.value, actor, str(session_id),
                 str(repo) if repo else None, int(tier) if tier is not None else None,
                 event.canonical_payload(), prev_hash, event.event_hash,
                 event.schema_version, event.host, event.pid, event.request_id),
            )
            # event_id is the table's AUTOINCREMENT primary key, assigned by
            # SQLite on insert — never generate/supply it ourselves.
            event_id_int = tx.query("SELECT last_insert_rowid() as id")[0]["id"]
            event_id = EventId.from_string(f"evt-{event_id_int}")

            if projection:
                table, cols = projection
                cols = {**cols, "event_id": event_id_int}
                if table != "human_approvals":
                    cols["ts"] = event.ts
                names = ",".join(cols.keys())
                qs = ",".join(["?"] * len(cols))
                tx.execute(f"INSERT INTO {table} ({names}) VALUES ({qs})", tuple(cols.values()))

            return event_id

    def get_chain(self) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT event_id, payload_json, prev_hash, event_hash "
            "FROM audit_events ORDER BY event_id ASC"
        )

    def verify_chain(self) -> ChainVerificationResult:
        rows = self.get_chain()
        ledger = [
            LedgerRow(
                event_id=EventId.from_string(f"evt-{r['event_id']}"),
                canonical_json=r["payload_json"],
                prev_hash=r["prev_hash"],
                event_hash=r["event_hash"],
            )
            for r in rows
        ]
        return verify_ledger_chain(ledger)
