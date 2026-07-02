#!/usr/bin/env python3
"""
observability/dashboard.py :: read-only local observability views.

Two modes, no data ever leaves the machine:

  summary  (default)  - prints a compact terminal report: session costs, p50/p95
                        latency by component, retrieval quality, recent policy
                        violations and security events, open approvals.
  serve               - launches Datasette against the SQLite DB for an interactive
                        local web UI (read-only). Requires `pip install datasette`.

Usage:
    python observability/dashboard.py
    python observability/dashboard.py --window 7d
    python observability/dashboard.py serve --port 8001
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _db_path() -> str:
    dsn = os.environ.get("CLAUDE_ENV_DSN",
                         f"sqlite:///{HOME}/state/claude-env.db")
    return dsn.replace("sqlite:///", "")


def _pct(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def summary(window: str) -> int:
    from lib.db import get_db
    db = get_db()

    # crude window filter: '7d' -> SQLite datetime('now','-7 days')
    n = int("".join(c for c in window if c.isdigit()) or "30")
    unit = "days" if window.endswith("d") else "hours" if window.endswith("h") else "days"
    since = f"datetime('now','-{n} {unit}')"

    print(f"== claude-env observability (last {n} {unit}) ==\n")

    print("-- session cost (top 10) --")
    rows = db.query(
        f"SELECT session_id, repo, input_tokens, output_tokens, "
        f"ROUND(est_cost_usd,4) AS usd FROM metrics_sessions "
        f"WHERE started_at >= {since} ORDER BY est_cost_usd DESC LIMIT 10")
    if not rows:
        print("   (no sessions)")
    for r in rows:
        print(f"   {r['session_id'][:18]:18s} {str(r['repo'])[:14]:14s} "
              f"in={r['input_tokens']:>8} out={r['output_tokens']:>8} ${r['usd']}")

    print("\n-- latency by component (ms) --")
    comps = db.query(
        f"SELECT DISTINCT component FROM metrics_latency WHERE ts >= {since}")
    if not comps:
        print("   (no latency samples)")
    for c in comps:
        vals = [r["duration_ms"] for r in db.query(
            f"SELECT duration_ms FROM metrics_latency "
            f"WHERE component=? AND ts >= {since}", (c["component"],))]
        print(f"   {c['component']:22s} n={len(vals):>5} "
              f"p50={_pct(vals,50):>7.0f} p95={_pct(vals,95):>7.0f} "
              f"max={max(vals) if vals else 0:>7.0f}")

    print("\n-- retrieval quality (mean top1) --")
    rq = db.query(
        f"SELECT repo, ROUND(AVG(top1_score),3) AS avg_top1, COUNT(*) AS n "
        f"FROM metrics_retrieval_quality WHERE ts >= {since} GROUP BY repo")
    if not rq:
        print("   (no retrieval samples)")
    for r in rq:
        print(f"   {str(r['repo'])[:20]:20s} avg_top1={r['avg_top1']} n={r['n']}")

    print("\n-- recent policy violations --")
    pv = db.query(
        f"SELECT ts, repo, rule, path FROM policy_violations "
        f"WHERE ts >= {since} ORDER BY ts DESC LIMIT 10")
    if not pv:
        print("   (none)")
    for r in pv:
        print(f"   {r['ts'][:19]} {str(r['repo'])[:12]:12s} {r['rule'][:24]:24s} {r['path']}")

    print("\n-- security events by severity --")
    se = db.query(
        f"SELECT severity, category, COUNT(*) AS n FROM security_events "
        f"WHERE ts >= {since} GROUP BY severity, category ORDER BY n DESC")
    if not se:
        print("   (none)")
    for r in se:
        print(f"   {r['severity']:8s} {r['category']:22s} {r['n']}")

    print("\n-- open approvals --")
    oa = db.query("SELECT request_id, agent, action FROM human_approvals "
                  "WHERE decision='pending' OR decision IS NULL ORDER BY requested_at")
    if not oa:
        print("   (none)")
    for r in oa:
        print(f"   {r['request_id']} [{r['agent']}] {r['action'][:60]}")
    return 0


def serve(port: int) -> int:
    db = _db_path()
    if not Path(db).exists():
        print(f"database not found at {db}; run bootstrap first", file=sys.stderr)
        return 1
    unregister = None
    try:
        from lib.services import pick_port, register, unregister as _unreg
        port = pick_port(port)                       # preferred, else a free port
        register("dashboard", port)
        unregister = _unreg
    except Exception:
        pass
    print(f"dashboard (datasette) -> http://127.0.0.1:{port}")
    try:
        return subprocess.run(
            [sys.executable, "-m", "datasette", db, "--port", str(port),
             "--setting", "sql_time_limit_ms", "5000", "-o"]).returncode
    except FileNotFoundError:
        print("datasette not installed: pip install datasette", file=sys.stderr)
        return 1
    finally:
        if unregister:
            try:
                unregister("dashboard")
            except Exception:
                pass


def main() -> int:
    ap = argparse.ArgumentParser(description="Local observability dashboard")
    sub = ap.add_subparsers(dest="cmd")
    ap.add_argument("--window", default="30d", help="e.g. 24h, 7d, 30d")
    sv = sub.add_parser("serve")
    sv.add_argument("--port", type=int, default=8001)
    args = ap.parse_args()
    if args.cmd == "serve":
        return serve(args.port)
    return summary(args.window)


if __name__ == "__main__":
    raise SystemExit(main())
