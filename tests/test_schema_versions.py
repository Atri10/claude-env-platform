"""Guard: every SQL migration records its schema_version row. Run: pytest tests/ -q

Regression guard for the gap where 002_retention.sql applied but never recorded
version 2, leaving schema_version inconsistent (1 and 3 only).
"""
import sqlite3
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SQL = [_ROOT / "sql" / f for f in
        ("001_schema.sql", "002_retention.sql", "003_extensions.sql")]


def test_all_migrations_recorded():
    db = tempfile.mktemp(suffix=".db")
    con = sqlite3.connect(db)
    for f in _SQL:
        con.executescript(f.read_text())
    versions = sorted(r[0] for r in con.execute("SELECT version FROM schema_version"))
    assert versions == [1, 2, 3], f"schema_version gaps: {versions}"
