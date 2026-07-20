"""
Tests for session cost tracking: the SQLite metrics repository writes and the
SessionStart/SessionEnd hook that feeds it (hooks/session_metrics_hook.py).

Run: pytest claudenv/tests/test_session_metrics_hook.py -q
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
from pathlib import Path

from claudenv.adapters.observability.repositories import SQLiteMetricsRepository
from claudenv.adapters.persistence.sqlite.database import SQLiteDatabase
from claudenv.domain.observability.session_metrics import compute_cost

_SQL = Path(__file__).resolve().parents[1] / "_data" / "sql" / "001_schema.sql"


def _fresh_repo() -> SQLiteMetricsRepository:
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    db = SQLiteDatabase(dsn)
    db.apply_schema(str(_SQL))
    return SQLiteMetricsRepository(db)


# --------------------------------------------------------------------------
# Repository writes (ISessionMetricsRepository)
# --------------------------------------------------------------------------
def test_start_session_creates_row_once():
    repo = _fresh_repo()
    repo.start_session("s1", "myrepo")
    repo.start_session("s1", "myrepo")  # INSERT OR IGNORE -> no duplicate
    rows = repo._db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s1",))
    assert len(rows) == 1
    assert rows[0]["repo"] == "myrepo"
    assert rows[0]["ended_at"] is None


def test_set_usage_totals_overwrites_not_increments():
    repo = _fresh_repo()
    repo.start_session("s2", "myrepo")
    repo.set_usage_totals("s2", 1000, 500)
    repo.set_usage_totals("s2", 1200, 600)  # hook firing twice
    rows = repo._db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s2",))
    assert rows[0]["input_tokens"] == 1200   # overwritten, not 1000+1200
    assert rows[0]["output_tokens"] == 600
    assert abs(rows[0]["est_cost_usd"] - compute_cost(1200, 600)) < 1e-9


def test_end_session_sets_ended_at():
    repo = _fresh_repo()
    repo.start_session("s3", "myrepo")
    repo.end_session("s3")
    rows = repo._db.query("SELECT ended_at FROM metrics_sessions WHERE session_id=?", ("s3",))
    assert rows[0]["ended_at"] is not None


def test_cost_by_repo():
    repo = _fresh_repo()
    repo.start_session("s4", "repoA")
    repo.set_usage_totals("s4", 100, 50)
    repo.start_session("s5", "repoB")
    repo.set_usage_totals("s5", 200, 100)
    by_repo = {r.repo: r.spent_usd for r in repo.cost_by_repo("1970-01-01T00:00:00")}
    assert abs(by_repo["repoA"] - compute_cost(100, 50)) < 1e-4
    assert abs(by_repo["repoB"] - compute_cost(200, 100)) < 1e-4


# --------------------------------------------------------------------------
# Hook (SessionStart / SessionEnd)
# --------------------------------------------------------------------------
def _load_hook():
    spec = importlib.util.spec_from_file_location(
        "session_metrics_hook",
        Path(__file__).resolve().parents[1]
        / "adapters" / "hooks" / "session_metrics_hook.py",
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _FakeConfig:
    def __init__(self, repo: SQLiteMetricsRepository):
        self._repo = repo

    def get_session_metrics_repository(self) -> SQLiteMetricsRepository:
        return self._repo


def test_session_start_creates_row(tmp_path, monkeypatch):
    repo = _fresh_repo()
    mod = _load_hook()
    monkeypatch.setattr(mod, "get_config", lambda: _FakeConfig(repo))
    mod.run({"hook_event_name": "SessionStart", "session_id": "s1",
             "cwd": str(tmp_path / "myrepo")})
    rows = repo._db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s1",))
    assert len(rows) == 1
    assert rows[0]["repo"] == "myrepo"  # basename fallback


def test_session_end_sums_transcript(tmp_path, monkeypatch):
    repo = _fresh_repo()
    mod = _load_hook()
    transcript = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "ok",
                                "usage": {"input_tokens": 100, "output_tokens": 50}}})
        for _ in range(3)
    ]
    lines.append(json.dumps({"message": {"role": "user", "content": "hi"}}))
    transcript.write_text("\n".join(lines) + "\n")

    monkeypatch.setattr(mod, "get_config", lambda: _FakeConfig(repo))
    mod.run({"hook_event_name": "SessionEnd", "session_id": "s2",
             "cwd": str(tmp_path), "transcript_path": str(transcript)})

    rows = repo._db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s2",))
    assert rows[0]["input_tokens"] == 300    # 3 assistant messages * 100
    assert rows[0]["output_tokens"] == 150   # 3 * 50
    assert rows[0]["ended_at"] is not None
    assert rows[0]["est_cost_usd"] > 0


def test_session_end_is_idempotent(tmp_path, monkeypatch):
    repo = _fresh_repo()
    mod = _load_hook()
    transcript = tmp_path / "transcript.jsonl"
    lines = [
        json.dumps({"message": {"role": "assistant", "content": "ok",
                                "usage": {"input_tokens": 100, "output_tokens": 50}}})
        for _ in range(2)
    ]
    transcript.write_text("\n".join(lines) + "\n")

    monkeypatch.setattr(mod, "get_config", lambda: _FakeConfig(repo))
    payload = {"hook_event_name": "SessionEnd", "session_id": "s3",
               "cwd": str(tmp_path), "transcript_path": str(transcript)}
    mod.run(payload)
    mod.run(payload)  # fire twice
    rows = repo._db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s3",))
    assert rows[0]["input_tokens"] == 200      # not 400 — overwrite, not increment


def test_malformed_transcript_line_does_not_crash(tmp_path, monkeypatch):
    repo = _fresh_repo()
    mod = _load_hook()
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        "not json\n"
        + json.dumps({"message": {"role": "assistant", "content": "ok"}}) + "\n"
    )
    monkeypatch.setattr(mod, "get_config", lambda: _FakeConfig(repo))
    assert mod.run({"hook_event_name": "SessionEnd", "session_id": "s4",
                    "cwd": str(tmp_path), "transcript_path": str(transcript)}) == 0
    rows = repo._db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("s4",))
    assert rows[0]["input_tokens"] == 0


def test_missing_transcript_file_does_not_crash(tmp_path, monkeypatch):
    repo = _fresh_repo()
    mod = _load_hook()
    monkeypatch.setattr(mod, "get_config", lambda: _FakeConfig(repo))
    assert mod.run({"hook_event_name": "SessionEnd", "session_id": "s5",
                    "cwd": str(tmp_path),
                    "transcript_path": "/nonexistent/path.jsonl"}) == 0
