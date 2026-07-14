"""
claude-env :: Application - Audit Reporting - SessionReplay

Session-replay forensics, read from the tamper-evident ledger via
IDatabase. Nothing leaves the machine unless the operator exports the
rendered output.
"""
from __future__ import annotations

import json

from claudenv.ports.database import IDatabase


def _summarize(event_type: str, body: dict) -> str:
    """One-line human summary per event type."""
    if event_type == "tool_call":
        args = body.get("args", {})
        arg = (args.get("file_path") or args.get("path") or args.get("command")
               or json.dumps(args)[:60])
        return f"{body.get('tool')}({arg}) -> {body.get('result_kind')}"
    if event_type == "retrieval":
        return (f"query={body.get('query', '')[:60]!r} top_k={body.get('top_k')} "
                f"returned={body.get('returned')} reranked={body.get('reranked')}")
    if event_type == "policy_violation":
        return f"{body.get('decision', '').upper()} {body.get('path')} (rule: {body.get('rule')})"
    if event_type == "security_event":
        return f"[{body.get('severity')}] {body.get('category')}: {body.get('detail', '')[:80]}"
    if event_type in ("memory_read", "memory_write"):
        return (f"ns={body.get('namespace')} type={body.get('memory_type')} "
                f"{body.get('operation') or 'hits=' + str(body.get('hit_count'))}")
    if event_type == "agent_action":
        return f"{body.get('agent')}:{body.get('action')} {body.get('target') or ''}"
    if event_type.startswith("human_approval"):
        return (f"{body.get('request_id')} {body.get('decision', 'requested')} "
                f"{body.get('action', '')[:60]}")
    return json.dumps(body)[:100]


class SessionReplay:
    """Reconstructs session timelines from the audit ledger for forensics."""

    def __init__(self, db: IDatabase):
        self._db = db

    def list_recent(self, limit: int = 25) -> list[dict]:
        return self._db.query(
            "SELECT session_id, COUNT(*) AS events, MIN(ts) AS first, MAX(ts) AS last, "
            "GROUP_CONCAT(DISTINCT actor) AS actors "
            "FROM audit_events GROUP BY session_id ORDER BY MAX(ts) DESC LIMIT ?",
            (limit,))

    def get_timeline(self, session_id: str) -> list[dict]:
        rows = self._db.query(
            "SELECT event_id, ts, event_type, actor, repo, payload_json "
            "FROM audit_events WHERE session_id=? ORDER BY event_id", (session_id,))
        out = []
        for r in rows:
            try:
                body = json.loads(r["payload_json"]).get("body", {})
            except Exception:
                body = {}
            out.append({"event_id": r["event_id"], "ts": r["ts"],
                        "type": r["event_type"], "actor": r["actor"],
                        "repo": r["repo"], "summary": _summarize(r["event_type"], body),
                        "body": body})
        return out
