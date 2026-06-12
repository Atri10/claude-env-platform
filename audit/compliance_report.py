#!/usr/bin/env python3
"""
claude-env :: compliance evidence report
File: audit/compliance_report.py
Purpose:
    Generate auditor-ready evidence from the tamper-evident ledger: who/what
    accessed which repos, policy violations, security events, approval trails,
    session costs — plus a fresh hash-chain verification proving the ledger
    itself has not been altered. Everything is read from the local DB; nothing
    leaves the machine unless the operator exports the file.

Usage:
    python audit/compliance_report.py                       # 30d, markdown, stdout
    python audit/compliance_report.py --window 7d --repo payments
    python audit/compliance_report.py --format json --out report.json
    python audit/compliance_report.py --format csv  --out events.csv
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db                       # noqa: E402
from audit.audit_logger import AuditLogger      # noqa: E402


def _parse_window(win: str) -> datetime:
    m = re.fullmatch(r"(\d+)([dhw])", win.strip())
    if not m:
        raise SystemExit(f"bad --window '{win}' (use e.g. 24h, 7d, 4w)")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n),
             "w": timedelta(weeks=n)}[unit]
    return datetime.now(timezone.utc) - delta


def gather(window: str, repo: str | None) -> dict:
    db = get_db()
    since = _parse_window(window).strftime("%Y-%m-%dT%H:%M:%S")
    rfilter, rparams = ("AND repo=?", [repo]) if repo else ("", [])

    def q(sql: str, params=()) -> list[dict]:
        return db.query(sql, list(params) + rparams)

    chain_ok, broken_at = AuditLogger("report", actor="reporter").verify_chain()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window": window, "since": since, "repo": repo or "(all)",
        "chain": {"verified": chain_ok, "first_broken_event": broken_at,
                  "total_events": (db.query_one(
                      "SELECT COUNT(*) AS n FROM audit_events") or {}).get("n", 0)},
        "events_by_type": q(
            f"SELECT event_type, COUNT(*) AS n FROM audit_events "
            f"WHERE ts>=? {rfilter} GROUP BY event_type ORDER BY n DESC", [since]),
        "actors": q(
            f"SELECT actor, COUNT(*) AS n FROM audit_events "
            f"WHERE ts>=? {rfilter} GROUP BY actor ORDER BY n DESC", [since]),
        "top_tools": q(
            "SELECT tool, COUNT(*) AS n, "
            "SUM(CASE WHEN result_kind!='ok' THEN 1 ELSE 0 END) AS errors "
            "FROM tool_calls WHERE ts>=? GROUP BY tool ORDER BY n DESC LIMIT 15",
            [since])[: None if not repo else None],
        "policy_violations": q(
            f"SELECT ts, repo, tier, path, rule, decision, actor "
            f"FROM policy_violations WHERE ts>=? {rfilter} ORDER BY ts DESC LIMIT 200",
            [since]),
        "security_events": q(
            "SELECT ts, category, severity, detail, source FROM security_events "
            "WHERE ts>=? ORDER BY ts DESC LIMIT 200", [since]),
        "approvals": q(
            f"SELECT requested_at, request_id, agent, repo, action, decision, "
            f"decided_by, decided_at FROM human_approvals "
            f"WHERE requested_at>=? {rfilter} ORDER BY requested_at DESC LIMIT 200",
            [since]),
        "session_costs": q(
            f"SELECT session_id, repo, started_at, input_tokens, output_tokens, "
            f"round(est_cost_usd, 4) AS est_cost_usd FROM metrics_sessions "
            f"WHERE started_at>=? {rfilter} ORDER BY est_cost_usd DESC LIMIT 50",
            [since]),
    }


# -- renderers ---------------------------------------------------------------
def render_md(d: dict) -> str:
    out = io.StringIO()
    w = out.write
    chain = d["chain"]
    w(f"# claude-env compliance report\n\n")
    w(f"- generated: `{d['generated_at']}`\n- window: `{d['window']}` "
      f"(since `{d['since']}`)\n- repo: `{d['repo']}`\n")
    w(f"- **ledger integrity: "
      f"{'VERIFIED' if chain['verified'] else 'BROKEN at event ' + str(chain['first_broken_event'])}** "
      f"({chain['total_events']} chained events total)\n\n")

    def table(title: str, rows: list[dict]):
        w(f"## {title}\n\n")
        if not rows:
            w("_none in window_\n\n")
            return
        cols = list(rows[0].keys())
        w("| " + " | ".join(cols) + " |\n")
        w("|" + "|".join("---" for _ in cols) + "|\n")
        for r in rows:
            w("| " + " | ".join(str(r.get(c, "")).replace("|", "\\|")[:80]
                                for c in cols) + " |\n")
        w("\n")

    table("Events by type", d["events_by_type"])
    table("Actors", d["actors"])
    table("Top tools", d["top_tools"])
    table("Policy violations", d["policy_violations"])
    table("Security events", d["security_events"])
    table("Human approvals", d["approvals"])
    table("Session costs", d["session_costs"])
    return out.getvalue()


def render_csv(d: dict) -> str:
    """Flat CSV of the raw in-window ledger rows (evidence-grade export)."""
    db = get_db()
    rfilter, rparams = ("AND repo=?", [d["repo"]]) if d["repo"] != "(all)" else ("", [])
    rows = db.query(
        f"SELECT event_id, ts, event_type, actor, session_id, repo, tier, "
        f"prev_hash, event_hash FROM audit_events WHERE ts>=? {rfilter} "
        f"ORDER BY event_id", [d["since"]] + rparams)
    out = io.StringIO()
    if rows:
        wr = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
        wr.writeheader()
        wr.writerows(rows)
    return out.getvalue()


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate a compliance evidence report")
    ap.add_argument("--window", default="30d", help="e.g. 24h, 7d, 4w (default 30d)")
    ap.add_argument("--repo")
    ap.add_argument("--format", choices=["md", "json", "csv"], default="md")
    ap.add_argument("--out", help="write to file instead of stdout")
    args = ap.parse_args()

    data = gather(args.window, args.repo)
    rendered = {"md": render_md, "csv": render_csv,
                "json": lambda d: json.dumps(d, indent=2, default=str)}[args.format](data)
    if args.out:
        Path(args.out).write_text(rendered)
        print(f"report written -> {args.out}")
    else:
        print(rendered)
    return 0 if data["chain"]["verified"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
