#!/usr/bin/env python3
"""
claude-env :: incident mode (kill switch)
File: security/incident.py
Purpose:
    One command to freeze the platform during a suspected compromise or
    runaway-agent event, and one to lift it. Designed so a security responder
    needs zero platform knowledge.

`claude-env incident on --reason "..." [--by who]` does, in order:
    1. writes the INCIDENT marker ($CLAUDE_ENV_HOME/state/INCIDENT) —
       PolicyEngine.evaluate_path fails closed on EVERY surface while it exists
       (filesystem-policy MCP server, RAG indexer, Claude Code policy hook)
    2. denies every pending human approval (audited)
    3. snapshots the SQLite database to archive/incident-<ts>.db (online, WAL-safe)
    4. writes a critical security_event to the ledger

`claude-env incident off [--by who]` removes the marker and logs the lift.
`claude-env incident status` prints the current state.

The marker itself is JSON ({ts, by, reason}) so responders can see why the
platform is frozen without querying anything.
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
MARKER = HOME / "state" / "INCIDENT"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snapshot_db() -> str | None:
    """Online WAL-safe SQLite backup. Returns archive path (None for non-sqlite)."""
    dsn = os.environ.get("CLAUDE_ENV_DSN",
                         f"sqlite:///{HOME}/state/claude-env.db")
    if not dsn.startswith("sqlite"):
        return None  # PostgreSQL deployments: use pg_dump per RUNBOOK backup §
    src_path = Path(dsn.replace("sqlite:///", "")).expanduser()
    if not src_path.exists():
        return None
    dest_dir = HOME / "archive"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"incident-{_now().replace(':', '')}.db"
    src = sqlite3.connect(str(src_path))
    dst = sqlite3.connect(str(dest))
    with dst:
        src.backup(dst)
    src.close(); dst.close()
    return str(dest)


def cmd_on(reason: str, by: str) -> int:
    if MARKER.exists():
        print("incident mode is ALREADY active:")
        print(MARKER.read_text())
        return 0

    MARKER.parent.mkdir(parents=True, exist_ok=True)
    MARKER.write_text(json.dumps(
        {"ts": _now(), "by": by, "reason": reason}, indent=2) + "\n")
    print(f"INCIDENT marker written -> {MARKER}")
    print("  all policy evaluations now fail closed (MCP, indexer, hooks)")

    # deny everything pending — a frozen platform must not have live grants
    denied = 0
    try:
        from audit.audit_logger import AuditLogger
        from agents.orchestration.approval_gate import ApprovalGate
        log = AuditLogger(session_id="incident", actor=by)
        for req in ApprovalGate.list_open():
            log.human_approval_resolve(req["request_id"], "denied",
                                       decided_by=f"incident:{by}")
            denied += 1
        print(f"  pending approvals denied: {denied}")
        snap = _snapshot_db()
        if snap:
            print(f"  database snapshot: {snap}")
        log.security_event("incident", "critical",
                           f"incident mode ACTIVATED by {by}: {reason} "
                           f"(approvals_denied={denied}, snapshot={snap})")
    except Exception as exc:
        # the marker is already in place — enforcement holds even if bookkeeping fails
        print(f"  WARN: post-freeze bookkeeping incomplete: {exc}")
    return 0


def cmd_off(by: str) -> int:
    if not MARKER.exists():
        print("incident mode is not active")
        return 0
    detail = MARKER.read_text().strip()
    MARKER.unlink()
    print("INCIDENT marker removed — policy enforcement back to normal rules")
    try:
        from audit.audit_logger import AuditLogger
        AuditLogger(session_id="incident", actor=by).security_event(
            "incident", "high", f"incident mode LIFTED by {by}; was: {detail}")
    except Exception as exc:
        print(f"  WARN: could not write audit event: {exc}")
    return 0


def cmd_status() -> int:
    if MARKER.exists():
        print("incident mode: ACTIVE")
        print(MARKER.read_text())
        return 1
    print("incident mode: inactive")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="claude-env incident mode (kill switch)")
    ap.add_argument("action", choices=["on", "off", "status"])
    ap.add_argument("--reason", default="unspecified")
    ap.add_argument("--by", default=os.environ.get("USER", "operator"))
    args = ap.parse_args()
    if args.action == "on":
        return cmd_on(args.reason, args.by)
    if args.action == "off":
        return cmd_off(args.by)
    return cmd_status()


if __name__ == "__main__":
    raise SystemExit(main())
