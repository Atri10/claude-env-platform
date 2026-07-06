"""
claude-env :: audit logger
File: audit/audit_logger.py
Purpose:
    Append-only, tamper-evident event ledger. Every Claude Code action, MCP
    tool call, retrieval, memory op, security event, policy violation, and human
    approval is recorded as a row in audit_events, hash-chained to the previous
    row. Typed projection tables (agent_actions, tool_calls, ...) are written in
    the SAME transaction so they cannot drift from the ledger.

Tamper evidence:
    event_hash = sha256( prev_hash || canonical_json(payload) )
    The first ever row uses prev_hash = 'GENESIS'.
    verify_chain() recomputes every hash and reports the first break.
    Because the DB has BEFORE UPDATE/DELETE triggers on audit_events, the only
    way to alter history is to drop the table -- which is itself detectable
    (chain restarts from GENESIS and length drops).

Design choices:
    * Canonical JSON: sort_keys=True, separators=(',',':'), ensure_ascii=False.
      Deterministic so the same payload always hashes identically.
    * Single writer lock guarantees monotonic chaining under concurrency.
    * Actor / repo / session attribution are first-class columns.

Usage:
    from audit.audit_logger import AuditLogger
    log = AuditLogger(session_id="sess-2025-...", actor="backend-agent", repo="payments")
    log.tool_call(tool="filesystem-policy.read_file",
                  args={"path": "src/x.py"}, result_kind="ok", duration_ms=4)
    log.policy_violation(path=".env", rule="global_secret", decision="block", tier=1)
    ok, broken_at = log.verify_chain()
"""
from __future__ import annotations

import hashlib
import json
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db  # noqa: E402

_WRITE_LOCK = threading.Lock()
GENESIS = "GENESIS"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _canon(payload: dict) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _hash(prev_hash: str, canon_payload: str) -> str:
    return hashlib.sha256((prev_hash + canon_payload).encode("utf-8")).hexdigest()


class AuditLogger:
    def __init__(self, session_id: str, actor: str = "system",
                 repo: str | None = None, tier: int | None = None):
        self.db = get_db()
        self.session_id = session_id
        self.actor = actor
        self.repo = repo
        self.tier = tier

    # -- core append -------------------------------------------------------
    def _append(self, event_type: str, payload: dict,
                projection: tuple[str, dict] | None = None) -> int:
        """Append one chained event + optional typed projection, atomically."""
        with _WRITE_LOCK:
            ts = _now()
            full_payload = {
                "ts": ts, "event_type": event_type, "actor": self.actor,
                "session_id": self.session_id, "repo": self.repo,
                "tier": self.tier, "body": payload,
            }
            canon = _canon(full_payload)

            # prev-hash read + insert must be one atomic unit: BEGIN IMMEDIATE
            # takes the SQLite write lock before the SELECT, so a concurrent
            # writer (a different process/connection -- _WRITE_LOCK only
            # serializes this process) can't read the same "current tip" and
            # fork the chain. See lib/db.py::Database.tx().
            with self.db.tx(immediate=True) as cur:
                cur.execute(
                    "SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1")
                prev_row = cur.fetchone()
                prev_hash = prev_row["event_hash"] if prev_row else GENESIS
                event_hash = _hash(prev_hash, canon)

                cur.execute(
                    "INSERT INTO audit_events "
                    "(ts,event_type,actor,session_id,repo,tier,payload_json,prev_hash,event_hash) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (ts, event_type, self.actor, self.session_id, self.repo,
                     self.tier, canon, prev_hash, event_hash),
                )
                event_id = cur.lastrowid
                if projection:
                    table, cols = projection
                    cols = {**cols, "event_id": event_id}
                    # human_approvals tracks requested_at/decided_at, not ts
                    if table != "human_approvals":
                        cols["ts"] = ts
                    names = ",".join(cols.keys())
                    qs = ",".join(["?"] * len(cols))
                    cur.execute(f"INSERT INTO {table} ({names}) VALUES ({qs})",
                                tuple(cols.values()))
            return event_id

    # -- typed event helpers ----------------------------------------------
    def agent_action(self, agent: str, action: str, target: str | None = None,
                     summary: str | None = None, success: bool = True) -> int:
        return self._append("agent_action",
            {"agent": agent, "action": action, "target": target, "summary": summary},
            ("agent_actions", {"agent": agent, "action": action, "target": target,
                               "summary": summary, "success": 1 if success else 0}))

    def tool_call(self, tool: str, args: dict, result_kind: str,
                  duration_ms: int | None = None) -> int:
        return self._append("tool_call",
            {"tool": tool, "args": args, "result_kind": result_kind, "duration_ms": duration_ms},
            ("tool_calls", {"tool": tool, "args_json": _canon(args),
                            "result_kind": result_kind, "duration_ms": duration_ms}))

    def retrieval(self, repo: str, query: str, top_k: int, returned: int,
                  branch: str | None = None, max_score: float | None = None,
                  min_score: float | None = None, reranked: bool = False,
                  duration_ms: int | None = None) -> int:
        return self._append("retrieval",
            {"repo": repo, "branch": branch, "query": query, "top_k": top_k,
             "returned": returned, "reranked": reranked},
            ("retrieval_events", {"repo": repo, "branch": branch, "query": query,
                                  "top_k": top_k, "returned": returned,
                                  "max_score": max_score, "min_score": min_score,
                                  "reranked": 1 if reranked else 0,
                                  "duration_ms": duration_ms}))

    def memory_read(self, namespace: str, memory_type: str, query: str | None,
                    hit_count: int) -> int:
        return self._append("memory_read",
            {"namespace": namespace, "memory_type": memory_type,
             "query": query, "hit_count": hit_count},
            ("memory_reads", {"namespace": namespace, "memory_type": memory_type,
                              "query": query, "hit_count": hit_count}))

    def memory_write(self, namespace: str, memory_type: str, node_id: str | None,
                     operation: str) -> int:
        return self._append("memory_write",
            {"namespace": namespace, "memory_type": memory_type,
             "node_id": node_id, "operation": operation},
            ("memory_writes", {"namespace": namespace, "memory_type": memory_type,
                               "node_id": node_id, "operation": operation}))

    def security_event(self, category: str, severity: str, detail: str,
                       source: str | None = None) -> int:
        return self._append("security_event",
            {"category": category, "severity": severity, "detail": detail, "source": source},
            ("security_events", {"category": category, "severity": severity,
                                 "detail": detail, "source": source}))

    def policy_violation(self, path: str, rule: str, decision: str,
                         tier: int | None = None) -> int:
        return self._append("policy_violation",
            {"path": path, "rule": rule, "decision": decision, "tier": tier},
            ("policy_violations", {"repo": self.repo, "tier": tier, "path": path,
                                   "rule": rule, "decision": decision, "actor": self.actor}))

    def human_approval_request(self, agent: str, action: str,
                               tier: int | None = None) -> str:
        request_id = f"appr-{uuid.uuid4().hex[:12]}"
        self._append("human_approval_request",
            {"request_id": request_id, "agent": agent, "action": action, "tier": tier},
            ("human_approvals", {"request_id": request_id, "agent": agent,
                                 "repo": self.repo, "tier": tier, "action": action,
                                 "decision": "pending", "requested_at": _now()}))
        return request_id

    def human_approval_resolve(self, request_id: str, decision: str,
                               decided_by: str) -> int:
        eid = self._append("human_approval_resolve",
            {"request_id": request_id, "decision": decision, "decided_by": decided_by})
        self.db.execute(
            "UPDATE human_approvals SET decision=?, decided_by=?, decided_at=? "
            "WHERE request_id=?", (decision, decided_by, _now(), request_id))
        return eid

    # -- verification ------------------------------------------------------
    def verify_chain(self) -> tuple[bool, int | None]:
        """Recompute the full chain. Returns (ok, first_broken_event_id|None)."""
        rows = self.db.query(
            "SELECT event_id, payload_json, prev_hash, event_hash "
            "FROM audit_events ORDER BY event_id ASC")
        prev_hash = GENESIS
        for r in rows:
            expected = _hash(prev_hash, r["payload_json"])
            if expected != r["event_hash"] or r["prev_hash"] != prev_hash:
                return False, r["event_id"]
            prev_hash = r["event_hash"]
        return True, None


if __name__ == "__main__":
    # smoke test usable after bootstrap
    log = AuditLogger(session_id="smoke", actor="system", repo="demo", tier=0)
    log.tool_call("demo.tool", {"x": 1}, "ok", 3)
    log.policy_violation(".env", "global_secret", "block", tier=0)
    ok, broken = log.verify_chain()
    print(f"chain_ok={ok} broken_at={broken}")
