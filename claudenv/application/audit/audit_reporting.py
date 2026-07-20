"""
claude-env :: Application - Audit Reporting

Compliance evidence reports and session-replay forensics, read from the
tamper-evident ledger via IDatabase/IAuditRepository. Nothing leaves the
machine unless the operator exports the rendered output.
"""
from __future__ import annotations

import csv
import io
import json
import re
from datetime import UTC, datetime, timedelta

from claudenv.ports.audit import IAuditRepository
from claudenv.ports.database import IDatabase
import logging
logger = logging.getLogger(__name__)


def _parse_window(win: str) -> datetime:
    m = re.fullmatch(r"(\d+)([dhw])", win.strip())
    if not m:
        raise ValueError(f"bad window '{win}' (use e.g. 24h, 7d, 4w)")
    n, unit = int(m.group(1)), m.group(2)
    delta = {"h": timedelta(hours=n), "d": timedelta(days=n),
             "w": timedelta(weeks=n)}[unit]
    return datetime.now(UTC) - delta


class ComplianceReportGenerator:
    """Generates auditor-ready evidence reports from the audit ledger."""

    def __init__(self, db: IDatabase, audit_repo: IAuditRepository):
        self._db = db
        self._audit_repo = audit_repo

    def gather(self, window: str = "30d", repo: str | None = None) -> dict:
        since = _parse_window(window).strftime("%Y-%m-%dT%H:%M:%S")
        rfilter, rparams = ("AND repo=?", [repo]) if repo else ("", [])

        def q(sql: str, params=()) -> list[dict]:
            return self._db.query(sql, tuple(list(params) + rparams))

        chain = self._audit_repo.verify_chain()

        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "window": window, "since": since, "repo": repo or "(all)",
            "chain": {"verified": chain.ok, "first_broken_event": chain.broken_at,
                      "total_events": chain.total_events},
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
                [since]),
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

    def _render_markdown(self, d: dict) -> str:
        out = io.StringIO()
        w = out.write
        chain = d["chain"]
        w("# claude-env compliance report\n\n")
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

    def _render_csv(self, d: dict) -> str:
        """Flat CSV of the raw in-window ledger rows (evidence-grade export)."""
        rfilter, rparams = ("AND repo=?", [d["repo"]]) if d["repo"] != "(all)" else ("", [])
        rows = self._db.query(
            f"SELECT event_id, ts, event_type, actor, session_id, repo, tier, "
            f"prev_hash, event_hash FROM audit_events WHERE ts>=? {rfilter} "
            f"ORDER BY event_id", tuple([d["since"]] + rparams))
        out = io.StringIO()
        if rows:
            wr = csv.DictWriter(out, fieldnames=list(rows[0].keys()))
            wr.writeheader()
            wr.writerows(rows)
        return out.getvalue()

    def generate(self, window: str = "30d", repo: str | None = None, format: str = "markdown") -> str:
        """Generate a rendered compliance report ('markdown', 'csv', or 'json')."""
        data = self.gather(window, repo)
        if format in ("markdown", "md"):
            return self._render_markdown(data)
        if format == "csv":
            return self._render_csv(data)
        if format == "json":
            return json.dumps(data, indent=2, default=str)
        raise ValueError(f"unknown format '{format}'")


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
                logger.warning("failed to parse audit event payload; using empty body", exc_info=True)
                body = {}
            out.append({"event_id": r["event_id"], "ts": r["ts"],
                        "type": r["event_type"], "actor": r["actor"],
                        "repo": r["repo"], "summary": _summarize(r["event_type"], body),
                        "body": body})
        return out
