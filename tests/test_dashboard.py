"""Coverage for the new per-repo cost total section in
observability/dashboard.py::summary(). Run: pytest tests/ -q
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


def test_summary_prints_cost_by_repo_total(capsys):
    db = _fresh_db()
    from observability.collectors import start_session, set_usage_totals
    start_session("s1", "payments")
    set_usage_totals("s1", 1_000_000, 0)   # $3.00
    start_session("s2", "payments")
    set_usage_totals("s2", 1_000_000, 0)   # $3.00 -- same repo, two sessions
    start_session("s3", "web-app")
    set_usage_totals("s3", 2_000_000, 0)   # $6.00

    import observability.dashboard as dashboard
    importlib.reload(dashboard)
    assert dashboard.summary("30d") == 0

    out = capsys.readouterr().out
    assert "cost by repo (total" in out
    payments_line = next(l for l in out.splitlines() if "payments" in l and "sessions=" in l)
    assert "sessions=   2" in payments_line or "sessions=2" in payments_line.replace(" ", "")
    assert "$6.0" in payments_line
