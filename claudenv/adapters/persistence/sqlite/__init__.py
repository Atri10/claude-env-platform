"""
claude-env :: Adapters - SQLite Persistence
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from claudenv.domain.value_objects import (
    BranchName, ChunkId, ContentHash, EdgeId, EventId, NodeId, RepoSlug,
    RequestId, SessionId, Tier, TableName, utc_now,
)
from claudenv.domain.audit import (
    AuditEvent, ChainVerificationResult, EventType, ProjectionBuilder, GENESIS, verify_chain,
)
from claudenv.domain.memory import (
    EdgeRelation, MemoryEdge, MemoryNode, MemoryType, NodeKind, Namespace,
)
from claudenv.domain.rag import (
    Chunk, FileState, IndexState, RetrievalMode, RetrievalQuery, RetrievalResult,
)
from claudenv.domain.policy import (
    CompiledPolicy, PolicyDecision, PolicyRuleSet, RepoPolicy,
)
from claudenv.ports import (
    IApprovalRepository, IAuditLogger, IAuditRepository, IConfigProvider,
    IDatabase, IEventBus, IMemoryRepository, IRagBookkeeping,
    IRagIndexer, IRagRetriever, IServiceRegistry,
)


# ============================================================================
# Database Adapter
# ============================================================================

class SQLiteDatabase:
    """SQLite database adapter implementing IDatabase."""

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(
            "CLAUDE_ENV_DSN",
            f"sqlite:///{Path.home()}/.claude-env/state/claude-env.db",
        )
        if not self.dsn.startswith("sqlite:///"):
            raise ValueError(f"Only SQLite DSN supported currently: {self.dsn}")
        self.path = self.dsn.replace("sqlite:///", "")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()

    @property
    def _conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn"):
            conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA foreign_keys=ON;")
            conn.execute("PRAGMA busy_timeout=5000;")
            self._local.conn = conn
        return self._local.conn

    @contextmanager
    def tx(self, immediate: bool = False):
        """Transaction context manager."""
        conn = self._conn
        cursor = conn.cursor()
        try:
            cursor.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield cursor
            cursor.execute("COMMIT")
        except Exception:
            cursor.execute("ROLLBACK")
            raise

    def query(self, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        cols = [c[0] for c in cursor.description] if cursor.description else []
        return [dict(zip(cols, row)) for row in cursor.fetchall()]

    def query_one(self, sql: str, params: tuple = ()) -> dict[str, Any] | None:
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        row = cursor.fetchone()
        return dict(row) if row else None

    def execute(self, sql: str, params: tuple = ()) -> int:
        cursor = self._conn.cursor()
        cursor.execute(sql, params)
        return cursor.lastrowid

    def executemany(self, sql: str, seq: list[tuple]) -> None:
        cursor = self._conn.cursor()
        cursor.executemany(sql, seq)

    def apply_schema(self, *sql_files: str) -> None:
        for f in sql_files:
            sql = Path(f).read_text()
            self._conn.executescript(sql)

    def close(self) -> None:
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            del self._local.conn


# ============================================================================
# Audit Repository
# ============================================================================

class SqliteAuditRepository:
    """SQLite implementation of IAuditRepository."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db

    def append(self, event: AuditEvent) -> None:
        with self.db.tx(immediate=True) as cur:
            # Insert main event
            cur.execute(
                """INSERT INTO audit_events
                   (event_id, ts, event_type, actor, session_id, repo, tier, payload_json, prev_hash, event_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    str(event.event_id), event.ts, event.event_type.value,
                    event.actor, str(event.session_id),
                    str(event.repo) if event.repo else None,
                    int(event.tier) if event.tier is not None else None,
                    json.dumps(event.payload, sort_keys=True, separators=(",", ":")),
                    event.prev_hash, event.event_hash,
                ),
            )

            # Insert projection based on event type
            self._insert_projection(cur, event)

    def _insert_projection(self, cur: sqlite3.Cursor, event: AuditEvent) -> None:
        p = ProjectionBuilder()

        if event.event_type == EventType.TOOL_CALL:
            proj = p.tool_call(event)
            cur.execute(
                """INSERT INTO tool_calls (event_id, ts, tool, args_json, result_kind, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(proj.event_id), proj.ts, proj.tool, proj.args_json, proj.result_kind, proj.duration_ms),
            )

        elif event.event_type == EventType.AGENT_ACTION:
            proj = p.agent_action(event)
            cur.execute(
                """INSERT INTO agent_actions (event_id, ts, agent, action, target, summary, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (str(proj.event_id), proj.ts, proj.agent, proj.action, proj.target, proj.summary, proj.success),
            )

        elif event.event_type == EventType.RETRIEVAL:
            proj = p.retrieval(event)
            cur.execute(
                """INSERT INTO retrieval_events (event_id, ts, repo, branch, query, top_k, returned, reranked, duration_ms)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(proj.event_id), proj.ts, proj.repo, proj.branch, proj.query, proj.top_k, proj.returned, proj.reranked, proj.duration_ms),
            )

        elif event.event_type == EventType.MEMORY_READ:
            proj = p.memory_read(event)
            cur.execute(
                """INSERT INTO memory_reads (event_id, ts, namespace, memory_type, query, hit_count)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(proj["event_id"]), proj["ts"], proj["namespace"], proj["memory_type"], proj["query"], proj["hit_count"]),
            )

        elif event.event_type == EventType.MEMORY_WRITE:
            proj = p.memory_write(event)
            cur.execute(
                """INSERT INTO memory_writes (event_id, ts, namespace, memory_type, node_id, operation)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(proj["event_id"]), proj["ts"], proj["namespace"], proj["memory_type"], proj["node_id"], proj["operation"]),
            )

        elif event.event_type == EventType.SECURITY_EVENT:
            proj = p.security_event(event)
            cur.execute(
                """INSERT INTO security_events (event_id, ts, category, severity, detail, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (str(proj.event_id), proj.ts, proj.category, proj.severity, proj.detail, proj.source),
            )

        elif event.event_type == EventType.POLICY_VIOLATION:
            proj = p.policy_violation(event)
            cur.execute(
                """INSERT INTO policy_violations (event_id, ts, repo, tier, path, rule, decision, actor)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(proj.event_id), proj.ts, proj.repo, proj.tier, proj.path, proj.rule, proj.decision, proj.actor),
            )

        elif event.event_type == EventType.HUMAN_APPROVAL_REQUEST:
            proj = p.human_approval_request(event)
            cur.execute(
                """INSERT INTO human_approvals (request_id, event_id, agent, repo, tier, action, decision, requested_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (str(proj.request_id), str(proj.event_id), proj.agent, proj.repo, proj.tier, proj.action, proj.decision, proj.ts),
            )

        elif event.event_type == EventType.HUMAN_APPROVAL_RESOLVE:
            proj = p.human_approval_resolve(event)
            cur.execute(
                """UPDATE human_approvals SET decision=?, decided_by=?, decided_at=? WHERE request_id=?""",
                (proj.decision, proj.decided_by, proj.decided_at, str(proj.request_id)),
            )

    def get_events(self, limit: int = 1000, offset: int = 0) -> list[AuditEvent]:
        rows = self.db.query(
            "SELECT * FROM audit_events ORDER BY event_id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        return [self._row_to_event(r) for r in rows]

    def get_events_since(self, since_id: EventId) -> list[AuditEvent]:
        rows = self.db.query(
            "SELECT * FROM audit_events WHERE event_id > ? ORDER BY event_id ASC",
            (str(since_id),),
        )
        return [self._row_to_event(r) for r in rows]

    def verify_chain(self) -> ChainVerificationResult:
        rows = self.db.query("SELECT * FROM audit_events ORDER BY event_id ASC")
        events = [self._row_to_event(r) for r in rows]
        return verify_chain(events)

    def _row_to_event(self, row: dict[str, Any]) -> AuditEvent:
        return AuditEvent(
            event_id=EventId.from_string(row["event_id"]),
            ts=row["ts"],
            event_type=EventType(row["event_type"]),
            actor=row["actor"],
            session_id=SessionId.from_string(row["session_id"]),
            repo=RepoSlug.from_string(row["repo"]) if row["repo"] else None,
            tier=Tier(row["tier"]) if row["tier"] is not None else None,
            payload=json.loads(row["payload_json"]),
            prev_hash=row["prev_hash"],
            event_hash=row["event_hash"],
        )


# ============================================================================
# Memory Repository
# ============================================================================

class SqliteMemoryRepository:
    """SQLite implementation of IMemoryRepository."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db

    def add_node(self, node: MemoryNode) -> None:
        self.db.execute(
            """INSERT INTO memory_nodes
               (node_id, namespace, memory_type, node_kind, name, body_json, repo, confidence,
                half_life_days, created_at, updated_at, last_access, access_count, embedding, superseded_by)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                str(node.node_id), node.namespace, node.memory_type.value, node.node_kind.value,
                node.name, json.dumps(node.body), node.repo, node.confidence,
                node.half_life_days,
                node.created_at.isoformat(), node.updated_at.isoformat(),
                node.last_access.isoformat(), node.access_count,
                node.embedding, node.superseded_by,
            ),
        )

    def get_node(self, node_id: NodeId, namespace: str) -> MemoryNode | None:
        row = self.db.query_one(
            "SELECT * FROM memory_nodes WHERE node_id=? AND namespace=?",
            (str(node_id), namespace),
        )
        return self._row_to_node(row) if row else None

    def list_nodes(
        self,
        namespace: str,
        memory_type: MemoryType | None = None,
        min_confidence: float = 0.0,
        include_superseded: bool = False,
        limit: int = 100,
    ) -> list[MemoryNode]:
        sql = "SELECT * FROM memory_nodes WHERE namespace=?"
        params: list[Any] = [namespace]
        if memory_type:
            sql += " AND memory_type=?"; params.append(memory_type.value)
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.db.query(sql, tuple(params))
        nodes = [self._row_to_node(r) for r in rows]
        return [n for n in nodes if n.effective_confidence >= min_confidence]

    def update_node(self, node: MemoryNode) -> None:
        self.db.execute(
            """UPDATE memory_nodes SET
                name=?, body_json=?, repo=?, confidence=?, half_life_days=?,
                updated_at=?, last_access=?, access_count=?, embedding=?, superseded_by=?
               WHERE node_id=? AND namespace=?""",
            (
                node.name, json.dumps(node.body), node.repo, node.confidence, node.half_life_days,
                node.updated_at.isoformat(), node.last_access.isoformat(), node.access_count,
                node.embedding, node.superseded_by,
                str(node.node_id), node.namespace,
            ),
        )

    def touch_node(self, node_id: NodeId, namespace: str) -> None:
        now = utc_now().isoformat()
        self.db.execute(
            "UPDATE memory_nodes SET last_access=?, access_count=access_count+1 WHERE node_id=? AND namespace=?",
            (now, str(node_id), namespace),
        )

    def add_edge(self, edge: MemoryEdge) -> None:
        self.db.execute(
            """INSERT INTO memory_edges (edge_id, namespace, src, dst, rel, weight, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                str(edge.edge_id), edge.namespace, str(edge.src), str(edge.dst),
                edge.relation.value, edge.weight, edge.created_at.isoformat(),
            ),
        )

    def get_edges(self, namespace: str, src: NodeId | None = None, dst: NodeId | None = None) -> list[MemoryEdge]:
        sql = "SELECT * FROM memory_edges WHERE namespace=?"
        params: list[Any] = [namespace]
        if src:
            sql += " AND src=?"; params.append(str(src))
        if dst:
            sql += " AND dst=?"; params.append(str(dst))
        rows = self.db.query(sql, tuple(params))
        return [self._row_to_edge(r) for r in rows]

    def expand(
        self,
        namespace: str,
        seed_ids: list[NodeId],
        depth: int,
        rels: list[EdgeRelation] | None = None,
    ) -> list[MemoryNode]:
        if not seed_ids:
            return []

        seed_ph = ",".join("?" * len(seed_ids))
        ns_ph = "?"  # namespace filter

        rel_filter = ""
        rel_params: list[Any] = []
        if rels:
            rel_ph = ",".join("?" * len(rels))
            rel_filter = f"AND e.rel IN ({rel_ph})"
            rel_params = [r.value for r in rels]

        sql = f"""
        WITH RECURSIVE walk(node_id, hops) AS (
            SELECT node_id, 0 FROM memory_nodes
             WHERE node_id IN ({seed_ph}) AND namespace={ns_ph}
            UNION
            SELECT e.dst, w.hops + 1
            FROM walk w JOIN memory_edges e ON e.src = w.node_id
            JOIN memory_nodes dn ON dn.node_id = e.dst
            WHERE w.hops < ? AND dn.namespace={ns_ph} {rel_filter}
        )
        SELECT DISTINCT n.* FROM walk w JOIN memory_nodes n ON n.node_id = w.node_id
        WHERE n.superseded_by IS NULL AND n.namespace={ns_ph}
        """

        params = [str(s) for s in seed_ids] + [namespace]  # seeds + namespace for anchor
        params += [depth] + [namespace] + rel_params  # recursive step
        params += [namespace]  # final projection

        rows = self.db.query(sql, tuple(params))
        return [self._row_to_node(r) for r in rows]

    def decay_all(self, namespace: str) -> int:
        rows = self.db.query(
            "SELECT node_id, confidence, half_life_days, updated_at FROM memory_nodes WHERE namespace=?",
            (namespace,),
        )
        count = 0
        for r in rows:
            from claudenv.domain.memory import effective_confidence
            eff = effective_confidence(r["confidence"], r["half_life_days"], r["updated_at"])
            self.db.execute(
                "UPDATE memory_nodes SET confidence=?, updated_at=? WHERE node_id=?",
                (round(eff, 4), utc_now().isoformat(), r["node_id"]),
            )
            count += 1
        return count

    def _row_to_node(self, row: dict[str, Any]) -> MemoryNode:
        return MemoryNode(
            node_id=NodeId.from_string(row["node_id"]),
            namespace=row["namespace"],
            memory_type=MemoryType(row["memory_type"]),
            node_kind=NodeKind(row["node_kind"]),
            name=row["name"],
            body=json.loads(row["body_json"]),
            repo=row["repo"],
            confidence=row["confidence"],
            half_life_days=row["half_life_days"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")),
            last_access=datetime.fromisoformat(row["last_access"].replace("Z", "+00:00")),
            access_count=row["access_count"],
            embedding=row["embedding"],
            superseded_by=row["superseded_by"],
        )

    def _row_to_edge(self, row: dict[str, Any]) -> MemoryEdge:
        return MemoryEdge(
            edge_id=EdgeId.from_string(row["edge_id"]),
            namespace=row["namespace"],
            src=NodeId.from_string(row["src"]),
            dst=NodeId.from_string(row["dst"]),
            relation=EdgeRelation(row["rel"]),
            weight=row["weight"],
            created_at=datetime.fromisoformat(row["created_at"].replace("Z", "+00:00")),
        )


# ============================================================================
# RAG Bookkeeping Repository
# ============================================================================

class SqliteRagBookkeepingRepository:
    """SQLite for RAG index state & file state."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db

    # Index state
    def upsert_index_state(self, state: IndexState) -> None:
        self.db.execute(
            """INSERT INTO rag_index_state (repo, branch, table_name, last_commit, chunk_count, embed_model, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(repo, branch) DO UPDATE SET
                 table_name=excluded.table_name, last_commit=excluded.last_commit,
                 chunk_count=excluded.chunk_count, updated_at=excluded.updated_at""",
            (
                str(state.repo), str(state.branch), state.table_name,
                state.last_commit, state.chunk_count, state.embed_model,
                state.updated_at.isoformat(),
            ),
        )

    def get_index_state(self, repo: RepoSlug, branch: BranchName) -> IndexState | None:
        row = self.db.query_one(
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

    # File state
    def upsert_file_state(self, state: FileState) -> None:
        self.db.execute(
            """INSERT INTO rag_file_state (repo, branch, file_path, content_hash, chunk_count, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(repo, branch, file_path) DO UPDATE SET
                 content_hash=excluded.content_hash, chunk_count=excluded.chunk_count,
                 indexed_at=excluded.indexed_at""",
            (
                str(state.repo), str(state.branch), state.file_path,
                str(state.content_hash), state.chunk_count, state.indexed_at.isoformat(),
            ),
        )

    def get_file_state(self, repo: RepoSlug, branch: BranchName, file_path: str) -> FileState | None:
        row = self.db.query_one(
            "SELECT * FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
            (str(repo), str(branch), file_path),
        )
        if not row:
            return None
        return FileState(
            repo=RepoSlug.from_string(row["repo"]),
            branch=BranchName.from_string(row["branch"]),
            file_path=row["file_path"],
            content_hash=ContentHash.from_string(row["content_hash"]),
            chunk_count=row["chunk_count"],
            indexed_at=datetime.fromisoformat(row["indexed_at"].replace("Z", "+00:00")),
        )

    def delete_file_state(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        self.db.execute(
            "DELETE FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
            (str(repo), str(branch), file_path),
        )


# ============================================================================
# Audit Logger Adapter
# ============================================================================

class SqliteAuditLogger:
    """SQLite implementation of IAuditLogger."""

    def __init__(self, db: SQLiteDatabase, session_id: SessionId, actor: str,
                 repo: RepoSlug | None = None, tier: Tier | None = None) -> None:
        self.repo = repo
        self.tier = tier
        self.audit_repo = SqliteAuditRepository(db)
        self._session_id = session_id
        self._actor = actor
        self._prev_hash = GENESIS
        self._lock = threading.Lock()

        # Initialize prev_hash from latest event
        latest = db.query_one("SELECT event_hash FROM audit_events ORDER BY event_id DESC LIMIT 1")
        if latest:
            self._prev_hash = latest["event_hash"]

    def _append(self, event_type: EventType, payload: dict[str, Any]) -> EventId:
        with self._lock:
            event = AuditEvent.create(
                event_type=event_type,
                actor=self._actor,
                session_id=self._session_id,
                payload=payload,
                repo=self.repo,
                tier=self.tier,
                prev_hash=self._prev_hash,
            )
            self.audit_repo.append(event)
            self._prev_hash = event.event_hash
            return event.event_id

    def tool_call(self, tool: str, args: dict, result_kind: str, duration_ms: int | None = None) -> EventId:
        return self._append(EventType.TOOL_CALL, {
            "tool": tool, "args": args, "result_kind": result_kind, "duration_ms": duration_ms,
        })

    def agent_action(self, agent: str, action: str, target: str | None = None,
                     summary: str | None = None, success: bool = True) -> EventId:
        return self._append(EventType.AGENT_ACTION, {
            "agent": agent, "action": action, "target": target, "summary": summary, "success": success,
        })

    def retrieval(self, repo: str, query: str, top_k: int, returned: int,
                  branch: str | None = None, reranked: bool = False, duration_ms: int | None = None) -> EventId:
        return self._append(EventType.RETRIEVAL, {
            "repo": repo, "branch": branch, "query": query, "top_k": top_k,
            "returned": returned, "reranked": reranked, "duration_ms": duration_ms,
        })

    def memory_read(self, namespace: str, memory_type: str, query: str | None, hit_count: int) -> EventId:
        return self._append(EventType.MEMORY_READ, {
            "namespace": namespace, "memory_type": memory_type, "query": query, "hit_count": hit_count,
        })

    def memory_write(self, namespace: str, memory_type: str, node_id: str, operation: str) -> EventId:
        return self._append(EventType.MEMORY_WRITE, {
            "namespace": namespace, "memory_type": memory_type, "node_id": node_id, "operation": operation,
        })

    def security_event(self, category: str, severity: str, detail: str, source: str | None = None) -> EventId:
        return self._append(EventType.SECURITY_EVENT, {
            "category": category, "severity": severity, "detail": detail, "source": source,
        })

    def policy_violation(self, path: str, rule: str, decision: str, tier: int | None = None) -> EventId:
        return self._append(EventType.POLICY_VIOLATION, {
            "repo": str(self.repo) if self.repo else "", "tier": tier, "path": path, "rule": rule, "decision": decision,
        })

    def human_approval_request(self, agent: str, action: str, tier: int | None = None) -> str:
        request_id = f"appr-{os.urandom(6).hex()}"
        self._append(EventType.HUMAN_APPROVAL_REQUEST, {
            "request_id": request_id, "agent": agent, "action": action, "tier": tier,
        })
        return request_id

    def human_approval_resolve(self, request_id: str, decision: str, decided_by: str) -> EventId:
        return self._append(EventType.HUMAN_APPROVAL_RESOLVE, {
            "request_id": request_id, "decision": decision, "decided_by": decided_by,
        })

    def verify_chain(self) -> ChainVerificationResult:
        return self.audit_repo.verify_chain()


# ============================================================================
# Service Registry
# ============================================================================

class SqliteServiceRegistry:
    """SQLite-backed local service registry."""

    def __init__(self, db: SQLiteDatabase) -> None:
        self.db = db

    def register(self, name: str, port: int, pid: int | None = None, extra: dict | None = None) -> dict:
        entry = {
            "name": name, "host": "127.0.0.1", "port": port,
            "url": f"http://127.0.0.1:{port}", "pid": pid or os.getpid(),
            "started_at": utc_now().isoformat(),
        }
        if extra:
            entry.update(extra)
        self.db.execute(
            """INSERT INTO services (name, host, port, url, pid, started_at, extra_json)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                 port=excluded.port, url=excluded.url, pid=excluded.pid,
                 started_at=excluded.started_at, extra_json=excluded.extra_json""",
            (name, "127.0.0.1", port, entry["url"], entry["pid"], entry["started_at"], json.dumps(extra or {})),
        )
        return entry

    def unregister(self, name: str) -> None:
        self.db.execute("DELETE FROM services WHERE name=?", (name,))

    def get(self, name: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM services WHERE name=?", (name,))
        return dict(row) if row else None

    def list_live(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM services")
        live = []
        for r in rows:
            try:
                import socket
                s = socket.socket()
                s.settimeout(0.3)
                s.connect((r["host"], r["port"]))
                s.close()
                live.append(dict(r))
            except OSError:
                pass
        return live


# Export with expected names
SQLiteDatabase = SQLiteDatabase
SQLiteAuditRepository = SqliteAuditRepository
SQLiteMemoryRepository = SqliteMemoryRepository
SQLiteRagBookkeepingRepository = SqliteRagBookkeepingRepository
SQLiteAuditLogger = SqliteAuditLogger
SQLiteServiceRegistry = SqliteServiceRegistry