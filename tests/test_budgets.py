"""Coverage for observability/budgets.py after removing the macOS
notification. Run: pytest tests/ -q
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


def test_notify_function_removed():
    import observability.budgets as budgets
    importlib.reload(budgets)
    assert not hasattr(budgets, "_notify")


def test_no_notify_flag_removed(monkeypatch, capsys):
    _fresh_db()
    import observability.budgets as budgets
    importlib.reload(budgets)
    monkeypatch.setattr(sys, "argv", ["budgets.py", "--no-notify"])
    with __import__("pytest").raises(SystemExit) as exc:
        budgets.main()
    assert exc.value.code == 2  # argparse: unrecognized argument


def test_evaluate_still_reports_spend(monkeypatch):
    db = _fresh_db()
    import observability.budgets as budgets
    importlib.reload(budgets)
    from observability.collectors import start_session, set_usage_totals
    start_session("s1", "payments")
    set_usage_totals("s1", 1_000_000, 0)  # $3.00 at default pricing
    result = budgets.evaluate()
    row = next(r for r in result["repos"] if r["repo"] == "payments")
    assert row["spent_usd"] == 3.0
