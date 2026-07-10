"""Coverage for observability/collectors.py session-cost writers.
Run: pytest tests/ -q
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _fresh_db():
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    d = db.get_db()
    d.apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    return d


def test_start_session_creates_row_once():
    db = _fresh_db()
    from observability.collectors import start_session
    start_session("sess-1", "myrepo")
    start_session("sess-1", "myrepo")  # second call must not error or duplicate
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("sess-1",))
    assert len(rows) == 1
    assert rows[0]["repo"] == "myrepo"


def test_set_usage_totals_overwrites_not_increments():
    db = _fresh_db()
    from observability.collectors import start_session, set_usage_totals
    start_session("sess-2", "myrepo")
    set_usage_totals("sess-2", 1000, 500)
    set_usage_totals("sess-2", 1200, 600)  # simulate the hook firing twice
    rows = db.query("SELECT * FROM metrics_sessions WHERE session_id=?", ("sess-2",))
    assert rows[0]["input_tokens"] == 1200   # overwritten, not 1000+1200
    assert rows[0]["output_tokens"] == 600
    expected_cost = (1200 * 3.00 + 600 * 15.00) / 1_000_000
    assert abs(rows[0]["est_cost_usd"] - expected_cost) < 1e-9


def test_end_session_sets_ended_at():
    db = _fresh_db()
    from observability.collectors import start_session, end_session
    start_session("sess-3", "myrepo")
    end_session("sess-3")
    rows = db.query("SELECT ended_at FROM metrics_sessions WHERE session_id=?", ("sess-3",))
    assert rows[0]["ended_at"] is not None
