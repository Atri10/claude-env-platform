"""
claude-env :: observability collectors
File: observability/collectors.py
Purpose:
    Lightweight, dependency-free metric writers. All metrics land in the same
    SQLite DB (metrics_* tables). These are called inline from the RAG pipeline,
    MCP servers, and orchestration. A separate dashboard (observability/dashboard.py)
    reads them. No data leaves the machine.

Cost model:
    est_cost_usd uses a configurable price table (per 1M tokens). Update PRICES
    to match the model you run Claude Code against. Defaults are placeholders.
"""
from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db   # noqa: E402

PRICES = {  # USD per 1M tokens (input, output) -- adjust to current pricing
    "default": (3.00, 15.00),
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def record_latency(component: str, operation: str, duration_ms: int,
                   repo: str | None = None) -> None:
    get_db().execute(
        "INSERT INTO metrics_latency (ts,component,operation,duration_ms,repo) "
        "VALUES (?,?,?,?,?)", (_now(), component, operation, duration_ms, repo))


def record_retrieval_quality(repo: str, query: str, top1: float | None,
                             mean_top_k: float | None,
                             rerank_delta: float | None = None) -> None:
    get_db().execute(
        "INSERT INTO metrics_retrieval_quality (ts,repo,query,top1_score,mean_top_k,rerank_delta) "
        "VALUES (?,?,?,?,?,?)", (_now(), repo, query, top1, mean_top_k, rerank_delta))


def start_session(session_id: str, repo: str | None = None) -> None:
    get_db().execute(
        "INSERT OR IGNORE INTO metrics_sessions (session_id,repo,started_at) "
        "VALUES (?,?,?)", (session_id, repo, _now()))


def record_tokens(session_id: str, input_tokens: int, output_tokens: int,
                  model: str = "default") -> None:
    pin, pout = PRICES.get(model, PRICES["default"])
    cost = (input_tokens * pin + output_tokens * pout) / 1_000_000
    get_db().execute(
        "UPDATE metrics_sessions SET input_tokens=input_tokens+?, "
        "output_tokens=output_tokens+?, est_cost_usd=est_cost_usd+? WHERE session_id=?",
        (input_tokens, output_tokens, cost, session_id))


def end_session(session_id: str) -> None:
    get_db().execute("UPDATE metrics_sessions SET ended_at=? WHERE session_id=?",
                     (_now(), session_id))
