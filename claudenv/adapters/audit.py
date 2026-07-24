"""
claude-env :: Adapters - SQLite Audit Logger
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
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
from claudenv.ports import IAuditLogger

GENESIS = "GENESIS"

logger = logging.getLogger(__name__)

# Identifies this OS process across every event it writes, so a report or
# replay can group "everything one running agent process did" without
# parsing timestamps. Cheap governance: costs nothing per-event, computed once.
_HOST = socket.gethostname()
_PID = os.getpid()


class SqliteAuditLogger(IAuditLogger):
    """SQLite implementation of IAuditLogger."""

    def __init__(
            self,
            db,
            session_id: SessionId,
            actor: str,
            repo: str | RepoSlug | None = None,
            tier: Tier | None = None,
    ) -> None:
        self.db = db
        self._session_id = session_id
        self._actor = actor
        # Every call site in this codebase (terminal/memory_graph/documentation/
        # lancedb_rag servers' create_server()) passes an already-constructed
        # RepoSlug here. Normalize once so _append() below doesn't re-wrap an
        # already-wrapped RepoSlug through RepoSlug.from_string(), which stored
        # the RepoSlug instance itself as .value and broke str(repo) (and broke
        # binding it as a SQL parameter) on every audited call.
        self._repo = repo if repo is None or isinstance(repo, RepoSlug) else RepoSlug.from_string(repo)
        self._tier = tier
        self._prev_hash = GENESIS
        self._lock = threading.Lock()

        # Initialize prev_hash from latest event
        latest = db.query_one("SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1")
        if latest:
            self._prev_hash = latest["event_hash"]

    def _append(self, event_type: EventType, payload: dict[str, Any]) -> EventId:
        with self._lock:
            # Use transaction with immediate lock for hash chain integrity
            with self.db.transaction(immediate=True) as tx:
                prev_row = tx.query(
                    "SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1")
                prev_hash = prev_row[0]["event_hash"] if prev_row else GENESIS

                # Domain owns canonical construction + hashing; the adapter
                # only supplies process-identity trace metadata and persists
                # the result. event_id/event_hash from create() are
                # provisional until the row lands — the real event_id comes
                # back from the autoincrement PK below.
                event = AuditEvent.create(
                    event_type=event_type,
                    actor=self._actor,
                    session_id=self._session_id,
                    payload=payload,
                    repo=self._repo,
                    tier=self._tier,
                    prev_hash=prev_hash,
                    host=_HOST,
                    pid=_PID,
                    request_id=f"req-{uuid.uuid4().hex[:16]}",
                )

                # Store the exact canonical envelope that was hashed — not a
                # reconstruction of it — so verify_chain never has to guess
                # what was actually signed.
                tx.execute(
                    """INSERT INTO audit_events
                       (ts, event_type, actor, session_id, repo, tier, payload_json,
                        prev_hash, event_hash, schema_version, host, pid, request_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (event.ts, event_type.value, self._actor, str(self._session_id),
                     str(self._repo) if self._repo else None, int(self._tier) if self._tier else None,
                     event.canonical_payload(), prev_hash, event.event_hash,
                     event.schema_version, event.host, event.pid, event.request_id),
                )
                # Get the auto-generated integer ID
                event_id_int = tx.query("SELECT last_insert_rowid() as id")[0]["id"]
                event_id = EventId.from_string(f"evt-{event_id_int}")

                # Write projection with the integer ID
                self._write_projection(tx, event_type, event_id_int, event.ts, payload)

                self._prev_hash = event.event_hash
                logger.debug(
                    "audit event appended: type=%s id=%s actor=%s repo=%s",
                    event_type.value, event_id, self._actor, self._repo,
                )
                return event_id

    def _write_projection(self, tx, event_type: EventType, event_id_int: int, ts: str, payload: dict) -> None:
        """Write event projection based on type using transaction."""
        if event_type == EventType.TOOL_CALL:
            tx.execute(
                """INSERT INTO tool_calls (event_id, ts, tool, args_json, result_kind, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id_int, ts, payload["tool"],
                 json.dumps(payload["args"], sort_keys=True, separators=(",", ":")),
                 payload["result_kind"], payload.get("duration_ms")),
            )

        elif event_type == EventType.AGENT_ACTION:
            tx.execute(
                """INSERT INTO agent_actions (event_id, ts, agent, action, target, summary, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (event_id_int, ts, payload["agent"], payload["action"],
                 payload.get("target"), payload.get("summary"), 1 if payload.get("success") else 0),
            )

        elif event_type == EventType.RETRIEVAL:
            tx.execute(
                """INSERT INTO retrieval_events (event_id, ts, repo, branch, query, top_k, returned, reranked,
                                                 duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id_int, ts, payload["repo"], payload.get("branch"),
                 payload["query"], payload["top_k"], payload["returned"],
                 1 if payload.get("reranked") else 0, payload.get("duration_ms")),
            )

        elif event_type == EventType.MEMORY_READ:
            tx.execute(
                """INSERT INTO memory_reads (event_id, ts, namespace, memory_type, query, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id_int, ts, payload["namespace"], payload["memory_type"],
                 payload.get("query"), payload["hit_count"]),
            )

        elif event_type == EventType.MEMORY_WRITE:
            tx.execute(
                """INSERT INTO memory_writes (event_id, ts, namespace, memory_type, node_id, operation)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id_int, ts, payload["namespace"], payload["memory_type"],
                 payload.get("node_id"), payload["operation"]),
            )

        elif event_type == EventType.SECURITY_EVENT:
            tx.execute(
                """INSERT INTO security_events (event_id, ts, category, severity, detail, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id_int, ts, payload["category"], payload["severity"],
                 payload["detail"], payload.get("source")),
            )

        elif event_type == EventType.POLICY_VIOLATION:
            tx.execute(
                """INSERT INTO policy_violations (event_id, ts, repo, tier, path, rule, decision, actor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (event_id_int, ts, payload.get("repo", self._repo), payload.get("tier"),
                 payload["path"], payload["rule"], payload["decision"], self._actor),
            )

        elif event_type == EventType.HUMAN_APPROVAL_REQUEST:
            request_id = payload["request_id"]
            tx.execute(
                """INSERT INTO human_approvals (request_id, event_id, agent, repo, tier, action, decision, requested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (request_id, event_id_int, payload["agent"],
                 str(self._repo) if self._repo else None,
                 int(payload["tier"]) if payload.get("tier") is not None else None,
                 payload["action"], "pending", ts),
            )

        elif event_type == EventType.HUMAN_APPROVAL_RESOLVE:
            request_id = payload["request_id"]
            tx.execute(
                """UPDATE human_approvals
                   SET decision=?, decided_by=?, decided_at=?
                   WHERE request_id = ?""",
                (payload["decision"], payload["decided_by"], ts, request_id),
            )

    def tool_call(self, tool: str, args: dict, result_kind: str, duration_ms: int | None = None) -> EventId:
        return self._append(EventType.TOOL_CALL, {
            "tool": tool, "args": args, "result_kind": result_kind, "duration_ms": duration_ms,
        })

    def agent_action(self, agent: str, action: str, target: str | None = None,
                     summary: str | None = None, success: bool = True) -> EventId:
        return self._append(EventType.AGENT_ACTION, {
            "agent": agent, "action": action, "target": target, "summary": summary, "success": success,
        })

    def retrieval(self, repo: str, query: str, top_k: int, returned: int,
                  branch: str | None = None, reranked: bool = False, duration_ms: int | None = None) -> EventId:
        return self._append(EventType.RETRIEVAL, {
            "repo": repo, "branch": branch, "query": query, "top_k": top_k,
            "returned": returned, "reranked": reranked, "duration_ms": duration_ms,
        })

    def memory_read(self, namespace: str, memory_type: str, query: str | None, hit_count: int) -> EventId:
        return self._append(EventType.MEMORY_READ, {
            "namespace": namespace, "memory_type": memory_type, "query": query, "hit_count": hit_count,
        })

    def memory_write(self, namespace: str, memory_type: str, node_id: str, operation: str) -> EventId:
        return self._append(EventType.MEMORY_WRITE, {
            "namespace": namespace, "memory_type": memory_type, "node_id": node_id, "operation": operation,
        })

    def security_event(self, category: str, severity: str, detail: str, source: str | None = None) -> EventId:
        return self._append(EventType.SECURITY_EVENT, {
            "category": category, "severity": severity, "detail": detail, "source": source,
        })

    def policy_violation(self, path: str, rule: str, decision: str, tier: int | None = None) -> EventId:
        return self._append(EventType.POLICY_VIOLATION, {
            "repo": str(self._repo) if self._repo else "", "tier": tier, "path": path, "rule": rule,
            "decision": decision,
        })

    def human_approval_request(self, agent: str, action: str, tier: int | None = None) -> str:
        request_id = f"appr-{os.urandom(6).hex()}"
        self._append(EventType.HUMAN_APPROVAL_REQUEST, {
            "request_id": request_id, "agent": agent, "action": action, "tier": tier,
        })
        return request_id

    def human_approval_resolve(self, request_id: str, decision: str, decided_by: str) -> EventId:
        return self._append(EventType.HUMAN_APPROVAL_RESOLVE, {
            "request_id": request_id, "decision": decision, "decided_by": decided_by,
        })

    def verify_chain(self) -> ChainVerificationResult:

        rows = self.db.query(
            "SELECT event_id, payload_json, prev_hash, event_hash "
            "FROM audit_events ORDER BY event_id ASC"
        )

        ledger = [
            LedgerRow(
                event_id=EventId.from_string(f"evt-{r['event_id']}"),
                canonical_json=r["payload_json"],
                prev_hash=r["prev_hash"],
                event_hash=r["event_hash"],
            )
            for r in rows
        ]

        result = verify_ledger_chain(ledger)
        if not result.ok:
            logger.error(
                "audit chain verification FAILED: broken at %s (checked %d events)",
                result.broken_at, result.total_events,
            )
        else:
            logger.debug("audit chain verified: %d events, all valid", len(rows))
        return result
