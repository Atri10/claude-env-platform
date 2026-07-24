"""
claude-env :: Adapters - SQLite observability repositories

Merges the previously separate feedback_repository.py, metrics_repository.py,
and projection_repository.py modules into one themed module.
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from claudenv.domain.observability.budget import RepoSpend
from claudenv.ports.database import IDatabase
from claudenv.ports.observability import (
    IFeedbackRepository,
    IMetricsRepository,
    IProjectionRepository,
    ISessionMetricsRepository,
)

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(UTC).isoformat()


class SQLiteFeedbackRepository(IFeedbackRepository):
    """IFeedbackRepository backed by the shared SQLite database.

    record_retrieved() and used_counts() are on the retrieval hot path and
    must NEVER raise — they fail silently. signal_totals() and
    top_used_files() are for observability surfaces and may propagate errors.
    """

    def __init__(self, db: IDatabase):
        self._db = db

    def record_retrieved(
        self,
        repo: str,
        branch: str,
        query_hash: str,
        chunks: list[dict[str, Any]],
        session_id: str,
    ) -> None:
        # Hot path: never break or slow a retrieval. Skip chunks with no id.
        try:
            ts = _now()
            params = [
                (
                    ts, repo, branch, c.get("chunk_id", "?"), c.get("file_path"),
                    query_hash, "retrieved", session_id,
                )
                for c in chunks
                if c.get("chunk_id")
            ]
            if params:
                self._db.executemany(
                    "INSERT INTO rag_chunk_feedback "
                    "(ts,repo,branch,chunk_id,file_path,query_hash,signal,session_id) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    params,
                )
        except Exception:
            logger.warning("feedback signal insert failed; ignored", exc_info=True)
            pass  # feedback must never break retrieval

    def used_counts(self, repo: str, branch: str) -> dict[str, int]:
        # Hot path (called after reranking): never raise.
        try:
            rows = self._db.query(
                "SELECT chunk_id, COUNT(*) AS n FROM rag_chunk_feedback "
                "WHERE repo=? AND branch=? AND signal='used' GROUP BY chunk_id",
                (repo, branch),
            )
            return {r["chunk_id"]: int(r["n"]) for r in rows}
        except Exception:
            logger.warning("feedback used_counts query failed; returning empty", exc_info=True)
            return {}

    def signal_totals(self, repo: str | None = None) -> dict[str, int]:
        # Observability surface: allowed to propagate errors.
        where, params = ("WHERE repo=?", (repo,)) if repo else ("", ())
        rows = self._db.query(
            f"SELECT signal, COUNT(*) AS n FROM rag_chunk_feedback {where} "
            f"GROUP BY signal",
            params,
        )
        return {r["signal"]: int(r["n"]) for r in rows}

    def top_used_files(self, repo: str | None = None, limit: int = 10) -> list[tuple[str, int]]:
        where, params = ("WHERE repo=? AND", (repo,)) if repo else ("WHERE", ())
        rows = self._db.query(
            f"SELECT file_path, COUNT(*) AS n FROM rag_chunk_feedback "
            f"{where} signal='used' GROUP BY file_path ORDER BY n DESC LIMIT ?",
            (*params, limit),
        )
        return [(r["file_path"], int(r["n"])) for r in rows]


class SQLiteMetricsRepository(IMetricsRepository, ISessionMetricsRepository):
    """Metrics repository backed by the shared SQLite database.

    Reads the observability metrics tables (metrics_sessions, metrics_latency,
    metrics_retrieval_quality) and writes session cost rows. Both read and
    write ports are satisfied by this single adapter.
    """

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

    def cost_by_repo(self, since_iso: str) -> list[RepoSpend]:
        rows = self._db.query(
            "SELECT COALESCE(repo,'(none)') AS repo, COUNT(*) AS sessions, "
            "ROUND(COALESCE(SUM(est_cost_usd),0), 4) AS spent_usd "
            "FROM metrics_sessions WHERE started_at>=? "
            "GROUP BY repo ORDER BY spent_usd DESC",
            (since_iso,),
        )
        return [
            RepoSpend(
                repo=r["repo"],
                sessions=int(r["sessions"] or 0),
                input_tokens=0,
                output_tokens=0,
                spent_usd=float(r["spent_usd"] or 0.0),
            )
            for r in rows
        ]

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

    # -- Session cost writes (ISessionMetricsRepository) --
    def start_session(self, session_id: str, repo: str | None) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO metrics_sessions (session_id, repo, started_at) "
            "VALUES (?, ?, ?)",
            (session_id, repo, _now()),
        )

    def set_usage_totals(
        self,
        session_id: str,
        input_tokens: int,
        output_tokens: int,
        model: str = "default",
    ) -> None:
        from claudenv.domain.observability.session_metrics import compute_cost

        cost = compute_cost(input_tokens, output_tokens, model)
        self._db.execute(
            "UPDATE metrics_sessions SET input_tokens=?, output_tokens=?, "
            "est_cost_usd=? WHERE session_id=?",
            (input_tokens, output_tokens, cost, session_id),
        )

    def end_session(self, session_id: str) -> None:
        self._db.execute(
            "UPDATE metrics_sessions SET ended_at=? WHERE session_id=?",
            (_now(), session_id),
        )
class SQLiteProjectionRepository(IProjectionRepository):
    """IProjectionRepository backed by the shared SQLite database.

    Read-only access to the audit projections the dashboard summarizes:
    policy_violations, security_events, human_approvals.
    """

    def __init__(self, db: IDatabase):
        self._db = db

    def recent_policy_violations(self, limit: int = 10) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT ts, COALESCE(repo,'(none)') AS repo, path, rule, decision, actor "
            "FROM policy_violations ORDER BY ts DESC LIMIT ?",
            (limit,),
        )

    def security_event_breakdown(self, since_iso: str) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT severity, category, COUNT(*) AS n FROM security_events "
            "WHERE ts>=? GROUP BY severity, category ORDER BY n DESC",
            (since_iso,),
        )

    def open_approvals(self) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT request_id, agent, COALESCE(repo,'(none)') AS repo, action, "
            "requested_at FROM human_approvals "
            "WHERE decision IS NULL OR decision='pending' "
            "ORDER BY requested_at DESC",
        )
