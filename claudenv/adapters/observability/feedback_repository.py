"""
claude-env :: Adapters - SQLite feedback repository

Reads/writes rag_chunk_feedback. record_retrieved() and used_counts() are on
the retrieval hot path and must NEVER raise — they fail silently. signal_totals()
and top_used_files() are for observability surfaces and may propagate errors.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from claudenv.ports.database import IDatabase
from claudenv.ports.observability import IFeedbackRepository


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteFeedbackRepository(IFeedbackRepository):
    """IFeedbackRepository backed by the shared SQLite database."""

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
