"""
claude-env :: Adapters - SQLite Persistence - Database & Transaction

Merges the previously separate database.py and transaction.py modules
(connection + transaction wrapper together).
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from claudenv.ports import ITransaction, IDatabase


class SQLiteTransaction(ITransaction):
    """SQLite transaction wrapper."""

    def __init__(self, conn: sqlite3.Connection, lock: threading.RLock, immediate: bool = False):
        self._conn = conn
        self._lock = lock
        self._cursor: sqlite3.Cursor | None = None
        self._immediate = immediate

    def __enter__(self) -> SQLiteTransaction:
        with self._lock:
            self._cursor = self._conn.cursor()
            self._cursor.execute("BEGIN IMMEDIATE" if self._immediate else "BEGIN")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type:
            self.rollback()
        else:
            self.commit()

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            self._cursor.execute(sql, params)
            return self._cursor.lastrowid

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            self._cursor.execute(sql, params)
            cols = [c[0] for c in self._cursor.description] if self._cursor.description else []
            return [dict(zip(cols, row)) for row in self._cursor.fetchall()]

    def commit(self) -> None:
        with self._lock:
            self._conn.commit()

    def rollback(self) -> None:
        with self._lock:
            self._conn.rollback()


class SQLiteDatabase(IDatabase):
    """SQLite database implementation."""

    def __init__(self, dsn: str):
        if not dsn.startswith("sqlite:///"):
            raise ValueError(f"Invalid SQLite DSN: {dsn}")
        path = dsn.replace("sqlite:///", "")
        Path(path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(Path(path).expanduser()),
            check_same_thread=False,
            isolation_level=None,  # autocommit; explicit tx via context manager
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA foreign_keys=ON;")
        self._conn.execute("PRAGMA busy_timeout=5000;")
        self._lock = threading.RLock()
        self._backend = "sqlite"

    @property
    def backend(self) -> str:
        return self._backend

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            cols = [c[0] for c in cur.description] if cur.description else []
            return [dict(zip(cols, row)) for row in cur.fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            row = cur.fetchone()
            return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        with self._lock:
            cur = self._conn.cursor()
            cur.execute(sql, params)
            return cur.lastrowid

    def executemany(self, sql: str, params: list[tuple]) -> None:
        with self._lock:
            cur = self._conn.cursor()
            cur.executemany(sql, params)

    @contextmanager
    def transaction(self, immediate: bool = False) -> ITransaction:
        tx = SQLiteTransaction(self._conn, self._lock, immediate=immediate)
        tx.__enter__()
        try:
            yield tx
            tx.commit()
        except Exception:
            tx.rollback()
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def apply_schema(self, *schema_files: str) -> None:
        """Apply SQL schema files in order.

        Idempotent: SQLite has no ADD COLUMN IF NOT EXISTS, so a migration
        that widens an existing table (see sql/004_audit_trace_metadata.sql)
        will raise "duplicate column name" on a later bootstrap re-run once
        the column already exists. That specific, well-understood error is
        swallowed; every other error still aborts and raises.
        """
        with self._lock:
            cur = self._conn.cursor()
            for schema_file in schema_files:
                path = Path(schema_file)
                if not path.exists():
                    raise FileNotFoundError(f"Schema file not found: {schema_file}")
                sql = path.read_text()
                try:
                    cur.executescript(sql)
                except sqlite3.OperationalError as e:
                    if "duplicate column name" not in str(e):
                        raise
            self._conn.commit()
