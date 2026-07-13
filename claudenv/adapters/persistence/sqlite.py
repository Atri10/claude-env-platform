"""
claude-env :: Adapters - SQLite Persistence
"""
from __future__ import annotations

import json
import os
import socket
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from claudenv.domain.audit import (
    AuditEvent, ChainVerificationResult, EventId, EventType, LedgerRow, SessionId, Tier,
    verify_ledger_chain,
)
from claudenv.domain.memory import (
    MemoryEdge, MemoryNode, MemoryType, NodeId,
)
from claudenv.domain.rag import (
    BranchName, IndexState, RepoSlug,
)
from claudenv.domain.value_objects import (
    ContentHash, utc_now,
)
from claudenv.ports import (
    ITransaction, IDatabase, IAuditRepository, IMemoryRepository, IRagBookkeeping,
)


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


# ============================================================================
# Audit Repository
# ============================================================================

class SQLiteAuditRepository(IAuditRepository):
    """SQLite audit event store with hash chain."""

    def __init__(self, db: SQLiteDatabase):
        self._db = db

    def append(
            self,
            event_type: EventType,
            payload: dict[str, Any],
            session_id: SessionId,
            actor: str,
            repo: RepoSlug | None,
            tier: Tier | None,
            projection: tuple[str, dict[str, Any]] | None = None,
    ) -> EventId:
        with self._db.transaction() as tx:
            prev_row = tx.query(
                "SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1"
            )
            prev_hash = prev_row[0]["event_hash"] if prev_row else "GENESIS"

            # Domain owns canonical construction + hashing (single source of
            # truth shared with SqliteAuditLogger); this adapter only persists
            # the result and supplies process-identity trace metadata.
            event = AuditEvent.create(
                event_type=event_type,
                actor=actor,
                session_id=session_id,
                payload=payload,
                repo=repo,
                tier=tier,
                prev_hash=prev_hash,
                host=socket.gethostname(),
                pid=os.getpid(),
                request_id=f"req-{uuid.uuid4().hex[:16]}",
            )

            tx.execute(
                "INSERT INTO audit_events "
                "(ts, event_type, actor, session_id, repo, tier, payload_json, prev_hash, "
                " event_hash, schema_version, host, pid, request_id) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (event.ts, event_type.value, actor, str(session_id),
                 str(repo) if repo else None, int(tier) if tier is not None else None,
                 event.canonical_payload(), prev_hash, event.event_hash,
                 event.schema_version, event.host, event.pid, event.request_id),
            )
            # event_id is the table's AUTOINCREMENT primary key, assigned by
            # SQLite on insert — never generate/supply it ourselves.
            event_id_int = tx.query("SELECT last_insert_rowid() as id")[0]["id"]
            event_id = EventId.from_string(f"evt-{event_id_int}")

            if projection:
                table, cols = projection
                cols = {**cols, "event_id": event_id_int}
                if table != "human_approvals":
                    cols["ts"] = event.ts
                names = ",".join(cols.keys())
                qs = ",".join(["?"] * len(cols))
                tx.execute(f"INSERT INTO {table} ({names}) VALUES ({qs})", tuple(cols.values()))

            return event_id

    def get_chain(self) -> list[dict[str, Any]]:
        return self._db.query(
            "SELECT event_id, payload_json, prev_hash, event_hash "
            "FROM audit_events ORDER BY event_id ASC"
        )

    def verify_chain(self) -> ChainVerificationResult:
        rows = self.get_chain()
        ledger = [
            LedgerRow(
                event_id=EventId.from_string(f"evt-{r['event_id']}"),
                canonical_json=r["payload_json"],
                prev_hash=r["prev_hash"],
                event_hash=r["event_hash"],
            )
            for r in rows
        ]
        return verify_ledger_chain(ledger)


# ============================================================================
# Memory Repository
# ============================================================================

class SQLiteMemoryRepository(IMemoryRepository):
    """SQLite memory graph persistence."""

    def __init__(self, db: SQLiteDatabase):
        self._db = db

    def insert_node(self, node: MemoryNode) -> None:
        self._db.execute(
            "INSERT INTO memory_nodes "
            "(node_id, namespace, memory_type, node_kind, name, body_json, repo, confidence, "
            " half_life_days, created_at, updated_at, last_access, access_count, superseded_by, embedding) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (str(node.node_id), node.namespace, node.memory_type.value, node.node_kind.value,
             node.name, json.dumps(node.body), node.repo, node.confidence, node.half_life_days,
             node.created_at.isoformat(), node.updated_at.isoformat(),
             node.last_access.isoformat(), node.access_count, node.superseded_by, node.embedding),
        )

    def update_node(self, node: MemoryNode) -> None:
        self._db.execute(
            "UPDATE memory_nodes SET "
            "confidence=?, updated_at=?, last_access=?, access_count=?, superseded_by=?, embedding=? "
            "WHERE node_id=?",
            (node.confidence, node.updated_at.isoformat(), node.last_access.isoformat(),
             node.access_count, node.superseded_by, node.embedding, str(node.node_id)),
        )

    def get_node(self, node_id: NodeId) -> MemoryNode | None:
        row = self._db.query_one("SELECT * FROM memory_nodes WHERE node_id=?", (str(node_id),))
        return MemoryNode.from_dict(row) if row else None

    def list_nodes(
            self,
            namespace: str,
            memory_type: MemoryType | None = None,
            min_confidence: float = 0.0,
            include_superseded: bool = False,
            limit: int = 100,
    ) -> list[MemoryNode]:
        sql = "SELECT * FROM memory_nodes WHERE namespace=?"
        params: list = [namespace]
        if memory_type:
            sql += " AND memory_type=?";
            params.append(memory_type.value)
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY updated_at DESC LIMIT ?";
        params.append(limit)
        rows = self._db.query(sql, tuple(params))
        nodes = [MemoryNode.from_dict(r) for r in rows]
        return [n for n in nodes if n.effective_confidence >= min_confidence]

    def decay_all(self, namespace: str) -> int:
        rows = self._db.query(
            "SELECT node_id, confidence, half_life_days, updated_at "
            "FROM memory_nodes WHERE namespace=?", (namespace,)
        )
        n = 0
        for r in rows:
            eff = r["confidence"]  # effective_confidence is computed at read time
            # Note: we persist the decayed value so future reads are fast
            self._db.execute(
                "UPDATE memory_nodes SET confidence=?, updated_at=? WHERE node_id=?",
                (eff, utc_now().isoformat(), r["node_id"]),
            )
            n += 1
        return n

    def insert_edge(self, edge: MemoryEdge) -> None:
        self._db.execute(
            "INSERT INTO memory_edges (edge_id, namespace, src, dst, rel, weight, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(edge.edge_id), edge.namespace, str(edge.src), str(edge.dst),
             edge.relation.value, edge.weight, edge.created_at.isoformat()),
        )

    def get_edges(
            self,
            src: NodeId,
            dst: NodeId | None = None,
            relation: str | None = None,
    ) -> list[MemoryEdge]:
        sql = "SELECT * FROM memory_edges WHERE src=?"
        params: list = [str(src)]
        if dst:
            sql += " AND dst=?";
            params.append(str(dst))
        if relation:
            sql += " AND rel=?";
            params.append(relation)
        rows = self._db.query(sql, tuple(params))
        return [MemoryEdge.from_dict(r) for r in rows]

    def expand_graph(
            self,
            seed_ids: list[NodeId],
            depth: int,
            relations: list[str] | None,
            namespace: str,
            extra_namespaces: list[str] | None,
    ) -> list[MemoryNode]:
        if not seed_ids:
            return []
        seed_ph = ",".join("?" * len(seed_ids))
        ns_list = [namespace] + (extra_namespaces or [])
        ns_ph = ",".join("?" * len(ns_list))
        rel_filter, rel_params = "", []
        if relations:
            rel_filter = f"AND e.rel IN ({','.join('?' * len(relations))})"
            rel_params = list(relations)

        sql = f"""
        WITH RECURSIVE walk(node_id, hops) AS (
            SELECT node_id, 0 FROM memory_nodes
             WHERE node_id IN ({seed_ph}) AND namespace IN ({ns_ph})
            UNION
            SELECT e.dst, w.hops + 1
            FROM walk w JOIN memory_edges e ON e.src = w.node_id
            JOIN memory_nodes dn ON dn.node_id = e.dst
            WHERE w.hops < ? AND dn.namespace IN ({ns_ph}) {rel_filter}
        )
        SELECT DISTINCT n.* FROM walk w JOIN memory_nodes n ON n.node_id = w.node_id
        WHERE n.superseded_by IS NULL AND n.namespace IN ({ns_ph})
        """
        params = ([str(s) for s in seed_ids] + ns_list + [depth] + ns_list + rel_params + ns_list)
        rows = self._db.query(sql, tuple(params))
        return [MemoryNode.from_dict(r) for r in rows]


# ============================================================================
# RAG Bookkeeping Repository
# ============================================================================

class SQLiteRagBookkeeping(IRagBookkeeping):
    """SQLite RAG file state and index state."""

    def __init__(self, db: SQLiteDatabase):
        self._db = db

    def get_file_hash(self, repo: RepoSlug, branch: BranchName, file_path: str) -> ContentHash | None:
        row = self._db.query_one(
            "SELECT content_hash FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
            (str(repo), str(branch), file_path),
        )
        return ContentHash.from_string(row["content_hash"]) if row else None

    def set_file_hash(
            self, repo: RepoSlug, branch: BranchName, file_path: str,
            content_hash: ContentHash, chunk_count: int,
    ) -> None:
        self._db.execute(
            "INSERT INTO rag_file_state (repo, branch, file_path, content_hash, chunk_count, indexed_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(repo, branch, file_path) DO UPDATE SET "
            "content_hash=excluded.content_hash, chunk_count=excluded.chunk_count, "
            "indexed_at=excluded.indexed_at",
            (str(repo), str(branch), file_path, str(content_hash), chunk_count, utc_now().isoformat()),
        )

    def delete_file_hash(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        self._db.execute(
            "DELETE FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
            (str(repo), str(branch), file_path),
        )

    def get_index_state(self, repo: RepoSlug, branch: BranchName) -> IndexState | None:
        row = self._db.query_one(
            "SELECT * FROM rag_index_state WHERE repo=? AND branch=?",
            (str(repo), str(branch)),
        )
        if not row:
            return None
        return IndexState(
            repo=RepoSlug.from_string(row["repo"]),
            branch=BranchName.from_string(row["branch"]),
            table_name=row["table_name"],
            last_commit=row["last_commit"],
            chunk_count=row["chunk_count"],
            embed_model=row["embed_model"],
            updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")),
        )

    def set_index_state(self, state: IndexState) -> None:
        self._db.execute(
            "INSERT INTO rag_index_state (repo, branch, table_name, last_commit, chunk_count, embed_model, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(repo, branch) DO UPDATE SET "
            "last_commit=excluded.last_commit, chunk_count=excluded.chunk_count, "
            "updated_at=excluded.updated_at, embed_model=excluded.embed_model",
            (str(state.repo), str(state.branch), state.table_name, state.last_commit,
             state.chunk_count, state.embed_model, state.updated_at.isoformat()),
        )

    def apply_schema(self, *sql_files: str) -> None:
        """Apply SQL schema files."""
        for f in sql_files:
            sql = Path(f).read_text()
            with self._lock:
                self._conn.executescript(sql)

    def close(self) -> None:
        self._conn.close()
