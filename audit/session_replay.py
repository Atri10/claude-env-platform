#!/usr/bin/env python3
"""
claude-env :: session replay / forensics
File: audit/session_replay.py
Purpose:
    Reconstruct exactly what happened in any session, step by step, from the
    tamper-evident ledger: every tool call, retrieval, memory op, policy
    decision, gate, and security event — in order, with timestamps. Answers
    "why did the agent do that?" after the fact.

Usage:
    python audit/session_replay.py --list                 # recent sessions
    python audit/session_replay.py <session_id>           # timeline (text)
    python audit/session_replay.py <session_id> --format json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db   # noqa: E402


def _summarize(event_type: str, body: dict) -> str:
    """One-line human summary per event type."""
    if event_type == "tool_call":
        args = body.get("args", {})
        arg = args.get("file_path") or args.get("path") or args.get("command") \
            or json.dumps(args)[:60]
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


def list_sessions(limit: int = 25) -> list[dict]:
    return get_db().query(
        "SELECT session_id, COUNT(*) AS events, MIN(ts) AS first, MAX(ts) AS last, "
        "GROUP_CONCAT(DISTINCT actor) AS actors "
        "FROM audit_events GROUP BY session_id ORDER BY MAX(ts) DESC LIMIT ?",
        (limit,))


def replay(session_id: str) -> list[dict]:
    rows = get_db().query(
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


def main() -> int:
    ap = argparse.ArgumentParser(description="Replay a session from the audit ledger")
    ap.add_argument("session_id", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    if args.list or not args.session_id:
        rows = list_sessions()
        if not rows:
            print("no sessions in the ledger yet")
            return 0
        print(f"{'session_id':38s} {'events':>6s}  {'first':24s} {'last':24s} actors")
        for r in rows:
            print(f"{r['session_id']:38s} {r['events']:>6d}  "
                  f"{r['first'][:23]:24s} {r['last'][:23]:24s} {r['actors']}")
        return 0

    events = replay(args.session_id)
    if not events:
        print(f"no events for session '{args.session_id}' "
              f"(use --list to see known sessions)")
        return 1
    if args.format == "json":
        print(json.dumps(events, indent=2, default=str))
        return 0
    print(f"# session {args.session_id} — {len(events)} events\n")
    for e in events:
        print(f"{e['ts'][:23]}  #{e['event_id']:<6d} {e['type']:22s} "
              f"[{e['actor']}] {e['summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
