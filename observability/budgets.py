#!/usr/bin/env python3
"""
claude-env :: cost budgets
File: observability/budgets.py
Purpose:
    Per-repo monthly USD budgets over metrics_sessions.est_cost_usd (calendar
    month to date). Configured in config/budgets.yaml; nothing here talks to
    any network — it is a local governance check.

    Exit codes: 0 ok / warning, 1 any budget exceeded — so it can gate CI or
    surface in a shell prompt. On macOS a notification fires at warn/exceed
    (best-effort, silent if osascript is unavailable).

Usage:
    python observability/budgets.py                 # status table
    python observability/budgets.py --repo payments
    python observability/budgets.py --format json
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import yaml                    # noqa: E402
from lib.db import get_db      # noqa: E402
from lib.logging_setup import get_logger  # noqa: E402

_log = get_logger("observability")

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))


def _config_path() -> Path:
    for cand in (HOME / "config" / "budgets.yaml",
                 _ROOT / "config" / "budgets.yaml"):
        if cand.exists():
            return cand
    return _ROOT / "config" / "budgets.yaml"


def load_config() -> dict:
    p = _config_path()
    doc = yaml.safe_load(p.read_text()) if p.exists() else {}
    doc = doc or {}
    monthly = doc.get("monthly_usd") or {}
    return {"warn_at": float(doc.get("warn_at", 0.8)),
            "default": float(monthly.get("default", 0) or 0),
            "repos": {k: float(v) for k, v in (monthly.get("repos") or {}).items()}}


def month_to_date_spend() -> list[dict]:
    month_start = datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00")
    return get_db().query(
        "SELECT COALESCE(repo,'(none)') AS repo, "
        "COUNT(*) AS sessions, SUM(input_tokens) AS input_tokens, "
        "SUM(output_tokens) AS output_tokens, "
        "ROUND(SUM(est_cost_usd), 4) AS spent_usd "
        "FROM metrics_sessions WHERE started_at>=? "
        "GROUP BY repo ORDER BY spent_usd DESC", (month_start,))


def evaluate(repo_filter: str | None = None) -> dict:
    cfg = load_config()
    rows = month_to_date_spend()
    if repo_filter:
        rows = [r for r in rows if r["repo"] == repo_filter]
    statuses, worst = [], "ok"
    for r in rows:
        budget = cfg["repos"].get(r["repo"], cfg["default"])
        spent = r["spent_usd"] or 0.0
        if budget <= 0:
            status, pct = "unlimited", None
        else:
            pct = spent / budget
            status = "EXCEEDED" if pct >= 1.0 else \
                     "warning" if pct >= cfg["warn_at"] else "ok"
        if status == "EXCEEDED":
            worst = "EXCEEDED"
        elif status == "warning" and worst != "EXCEEDED":
            worst = "warning"
        statuses.append({**r, "budget_usd": budget or None,
                         "pct": round(pct, 3) if pct is not None else None,
                         "status": status})
    return {"month": datetime.now(timezone.utc).strftime("%Y-%m"),
            "warn_at": cfg["warn_at"], "repos": statuses, "overall": worst}


def _notify(title: str, message: str) -> None:
    """Best-effort local notification (macOS only, never fails)."""
    if sys.platform != "darwin":
        return
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification "{message}" with title "{title}"'],
            capture_output=True, timeout=5)
    except Exception:
        # desktop notification is a nicety (macOS-only, may be absent/blocked);
        # never let it break budget enforcement.
        _log.debug("desktop notification failed: %s", title, exc_info=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Monthly cost budget status")
    ap.add_argument("--repo")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()

    result = evaluate(args.repo)
    if args.format == "json":
        print(json.dumps(result, indent=2))
    else:
        print(f"# budget status — month {result['month']} "
              f"(warn at {int(result['warn_at'] * 100)}%)")
        if not result["repos"]:
            print("no session spend recorded this month")
        else:
            print(f"{'repo':24s} {'sessions':>8s} {'spent USD':>10s} "
                  f"{'budget':>8s} {'used':>6s}  status")
            for r in result["repos"]:
                pct = f"{int(r['pct'] * 100)}%" if r["pct"] is not None else "-"
                budget = f"{r['budget_usd']:.0f}" if r["budget_usd"] else "-"
                print(f"{r['repo']:24s} {r['sessions']:>8d} "
                      f"{(r['spent_usd'] or 0):>10.2f} {budget:>8s} {pct:>6s}  {r['status']}")
    if result["overall"] != "ok" and not args.no_notify:
        bad = [r["repo"] for r in result["repos"]
               if r["status"] in ("warning", "EXCEEDED")]
        _notify("claude-env budget",
                f"{result['overall']}: {', '.join(bad[:3])}")
    return 1 if result["overall"] == "EXCEEDED" else 0


if __name__ == "__main__":
    raise SystemExit(main())
