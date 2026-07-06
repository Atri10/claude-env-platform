"""
claude-env :: persistence abstraction
File: lib/db.py
Purpose:
    Single chokepoint for all DB access so a future PostgreSQL migration is a
    config change, not a rewrite. Application code never imports sqlite3 or
    psycopg directly -- it calls get_db() and uses the Database facade.

Design:
    * DSN-driven backend selection: sqlite:///path  OR  postgresql://...
    * Paramstyle normalization: app code always writes ``?`` placeholders;
      the Postgres adapter rewrites them to ``%s``.
    * Thin, dependency-light. SQLite path uses stdlib only. The Postgres path
      imports psycopg lazily so it is not required for the default install.

Usage:
    from lib.db import get_db
    db = get_db()                      # reads CLAUDE_ENV_DSN or defaults to sqlite
    with db.tx() as cur:
        cur.execute("INSERT INTO t(a) VALUES(?)", (1,))
    rows = db.query("SELECT * FROM t WHERE a = ?", (1,))
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterable, Sequence

DEFAULT_DSN = f"sqlite:///{Path.home()}/.claude-env/state/claude-env.db"


class Database:
    """Backend-agnostic facade. Construct via get_db()."""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._lock = threading.RLock()
        if dsn.startswith("sqlite"):
            self.backend = "sqlite"
            path = dsn.replace("sqlite:///", "")
            Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(Path(path).expanduser()),
                check_same_thread=False,
                isolation_level=None,  # autocommit; explicit tx via context mgr
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA foreign_keys=ON;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
        elif dsn.startswith("postgres"):
            self.backend = "postgres"
            import psycopg  # lazy import; only needed on Postgres
            self._conn = psycopg.connect(dsn, autocommit=True)
        else:
            raise ValueError(f"Unsupported DSN scheme: {dsn}")

    # -- placeholder translation -------------------------------------------
    def _xlate(self, sql: str) -> str:
        if self.backend == "postgres":
            # naive but sufficient: app code never uses literal '?' in strings
            return sql.replace("?", "%s")
        return sql

    # -- read --------------------------------------------------------------
    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(self._xlate(sql), params)
            cols = [c[0] for c in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_one(self, sql: str, params: Sequence[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    # -- write -------------------------------------------------------------
    def execute(self, sql: str, params: Sequence[Any] = ()) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(self._xlate(sql), params)
            return cur.lastrowid if self.backend == "sqlite" else cur.rowcount

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executemany(self._xlate(sql), list(seq))

    @contextmanager
    def tx(self, immediate: bool = False):
        """Explicit transaction. Rolls back on exception.

        immediate=True acquires the SQLite write lock up front (BEGIN IMMEDIATE)
        instead of lazily on first write (plain BEGIN). Use this whenever a
        transaction reads state that must not change before it writes based on
        that state (e.g. read-current-tip-then-insert for a hash chain) -- across
        separate OS processes/connections, this app's in-process locks don't
        serialize anything, so without BEGIN IMMEDIATE two writers can both read
        the same "current" row and then both commit, forking the chain.
        """
        with self._lock:
            cur = self._conn.cursor()
            try:
                cur.execute("BEGIN IMMEDIATE" if immediate and self.backend == "sqlite"
                            else "BEGIN")
                yield cur
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def apply_schema(self, *sql_files: str) -> None:
        for f in sql_files:
            sql = Path(f).read_text()
            with self._lock:
                self._conn.executescript(sql) if self.backend == "sqlite" else \
                    self._conn.cursor().execute(sql)

    def close(self) -> None:
        self._conn.close()


_INSTANCE: Database | None = None


def get_db() -> Database:
    """Process-wide singleton selected by env var CLAUDE_ENV_DSN."""
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = Database(os.environ.get("CLAUDE_ENV_DSN", DEFAULT_DSN))
    return _INSTANCE
