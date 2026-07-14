"""
claude-env :: Adapters - SQLite Persistence - Transaction
"""
from __future__ import annotations

import sqlite3
import threading
from typing import Any

from claudenv.ports import ITransaction


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
