"""
claude-env :: Adapters - SQLite metrics repository

Reads the observability metrics tables (metrics_sessions, metrics_latency,
metrics_retrieval_quality) via IDatabase. Read-only aggregation — never writes.
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.observability.budget_evaluator import RepoSpend
from claudenv.ports.database import IDatabase
from claudenv.ports.observability import IMetricsRepository


class SQLiteMetricsRepository(IMetricsRepository):
    """IMetricsRepository backed by the shared SQLite database."""

    def __init__(self, db: IDatabase):
        self._db = db

    def month_to_date_spend(self, month_start_iso: str) -> list[RepoSpend]:
        rows = self._db.query(
            "SELECT COALESCE(repo,'(none)') AS repo, "
            "COUNT(*) AS sessions, "
            "COALESCE(SUM(input_tokens),0) AS input_tokens, "
            "COALESCE(SUM(output_tokens),0) AS output_tokens, "
            "ROUND(COALESCE(SUM(est_cost_usd),0), 4) AS spent_usd "
            "FROM metrics_sessions WHERE started_at>=? "
            "GROUP BY repo ORDER BY spent_usd DESC",
            (month_start_iso,),
        )
        return [
            RepoSpend(
                repo=r["repo"],
                sessions=int(r["sessions"] or 0),
                input_tokens=int(r["input_tokens"] or 0),
                output_tokens=int(r["output_tokens"] or 0),
                spent_usd=float(r["spent_usd"] or 0.0),
            )
            for r in rows
        ]

    def top_session_costs(self, since_iso: str, limit: int = 10) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT session_id, COALESCE(repo,'(none)') AS repo, "
            "input_tokens, output_tokens, ROUND(est_cost_usd,4) AS spent_usd, "
            "started_at FROM metrics_sessions WHERE started_at>=? "
            "ORDER BY est_cost_usd DESC LIMIT ?",
            (since_iso, limit),
        )

    def latency_by_component(self, since_iso: str) -> dict[str, list[float]]:
        rows = self._db.query(
            "SELECT component, duration_ms FROM metrics_latency WHERE ts>=?",
            (since_iso,),
        )
        out: dict[str, list[float]] = {}
        for r in rows:
            out.setdefault(r["component"], []).append(float(r["duration_ms"]))
        return out

    def retrieval_quality_by_repo(self, since_iso: str) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT repo, ROUND(AVG(top1_score),4) AS mean_top1, "
            "COUNT(*) AS queries FROM metrics_retrieval_quality WHERE ts>=? "
            "GROUP BY repo ORDER BY mean_top1 DESC",
            (since_iso,),
        )
