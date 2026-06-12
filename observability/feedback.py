"""
claude-env :: retrieval feedback loop
File: observability/feedback.py
Purpose:
    Turn retrieval observability into retrieval improvement.

    Signals (rag_chunk_feedback, sql/003_extensions.sql):
      'retrieved' — written by the retrieve pipeline for every chunk returned
      'used'      — written by memory/session_ingestor.py when a file behind a
                    retrieved chunk was edited in a Claude Code session shortly
                    after retrieval (heuristic correlation)

    The retrieve pipeline calls usage_boosts() after reranking and adds a small
    logarithmic boost for chunks with a history of actual use, so chunks that
    repeatedly help land higher. Disable with CLAUDE_ENV_FEEDBACK_BOOST=false.

    Boost = BOOST_UNIT * ln(1 + min(used_count, CAP)) — bounded so feedback can
    re-order near-ties but can never overrule the reranker on a clear loser.
"""
from __future__ import annotations

import hashlib
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db   # noqa: E402

BOOST_UNIT = 0.05
CAP = 20


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def boost_enabled() -> bool:
    return os.environ.get("CLAUDE_ENV_FEEDBACK_BOOST", "true").lower() != "false"


def query_hash(query: str) -> str:
    return hashlib.sha256(query.encode()).hexdigest()[:16]


def record_retrieved(repo: str, branch: str, query: str,
                     chunks: list[dict], session_id: str) -> None:
    """Record one 'retrieved' row per returned chunk. Never raises."""
    try:
        db = get_db()
        qh = query_hash(query)
        ts = _now()
        db.executemany(
            "INSERT INTO rag_chunk_feedback "
            "(ts,repo,branch,chunk_id,file_path,query_hash,signal,session_id) "
            "VALUES (?,?,?,?,?,?,?,?)",
            [(ts, repo, branch, c.get("chunk_id", "?"), c.get("file_path"),
              qh, "retrieved", session_id) for c in chunks if c.get("chunk_id")])
    except Exception:
        pass  # feedback must never break retrieval


def usage_boosts(repo: str, branch: str) -> dict[str, float]:
    """chunk_id -> boost, from historical 'used' signals. Never raises."""
    try:
        rows = get_db().query(
            "SELECT chunk_id, COUNT(*) AS n FROM rag_chunk_feedback "
            "WHERE repo=? AND branch=? AND signal='used' GROUP BY chunk_id",
            (repo, branch))
        return {r["chunk_id"]: BOOST_UNIT * math.log1p(min(r["n"], CAP))
                for r in rows}
    except Exception:
        return {}


def stats(repo: str | None = None) -> dict:
    """Aggregate feedback stats for the dashboard / CLI."""
    db = get_db()
    where, params = ("WHERE repo=?", (repo,)) if repo else ("", ())
    rows = db.query(
        f"SELECT signal, COUNT(*) AS n FROM rag_chunk_feedback {where} "
        f"GROUP BY signal", params)
    out = {r["signal"]: r["n"] for r in rows}
    top = db.query(
        f"SELECT file_path, COUNT(*) AS n FROM rag_chunk_feedback "
        f"{where + (' AND ' if where else 'WHERE ')} signal='used' "
        f"GROUP BY file_path ORDER BY n DESC LIMIT 10", params)
    out["top_used_files"] = [(r["file_path"], r["n"]) for r in top]
    return out
