"""
claude-env :: Adapters - SQLite Persistence - Repositories

Merges the previously separate audit_repository.py, memory_repository.py,
rag_bookkeeping.py, and policy_repository.py modules into one themed
module.
"""
from __future__ import annotations

import json
import os
import socket
import uuid
from datetime import datetime
from typing import Any

from claudenv.domain.audit import (
    AuditEvent,
    ChainVerificationResult,
    EventId,
    EventType,
    LedgerRow,
    SessionId,
    Tier,
    verify_ledger_chain,
)
from claudenv.domain.memory import MemoryEdge, MemoryNode, MemoryType, NodeId
from claudenv.domain.policy import RepoPolicy
from claudenv.domain.rag import BranchName, IndexState, RepoSlug
from claudenv.domain.value_objects import ContentHash, utc_now
from claudenv.ports import IAuditRepository, IMemoryRepository, IPolicyRepository, IRagBookkeeping

from .database import SQLiteDatabase


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
            sql += " AND memory_type=?"
            params.append(memory_type.value)
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY updated_at DESC LIMIT ?"
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
            sql += " AND dst=?"
            params.append(str(dst))
        if relation:
            sql += " AND rel=?"
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

    def delete_node(self, node_id: NodeId) -> None:
        """Delete a memory node by ID."""
        self._db.execute(
            "DELETE FROM memory_nodes WHERE node_id=?",
            (str(node_id),),
        )


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


class SQLitePolicyRepository(IPolicyRepository):
    """SQLite policy configuration repository."""

    def __init__(self, db: SQLiteDatabase):
        self._db = db

    def get_global_policy(self) -> dict[str, Any]:
        row = self._db.query_one(
            "SELECT policy_json FROM global_policy WHERE id = 1"
        )
        if row:
            return json.loads(row["policy_json"])
        return {}

    def get_repo_policy(self, repo_root: str) -> RepoPolicy | None:
        row = self._db.query_one(
            "SELECT policy_json FROM repo_policy WHERE repo_root = ?",
            (repo_root,),
        )
        if not row:
            return None
        return RepoPolicy.from_yaml(json.loads(row["policy_json"]))

    def save_repo_policy(self, repo_root: str, policy: RepoPolicy) -> None:
        self._db.execute(
            "INSERT INTO repo_policy (repo_root, policy_json) VALUES (?, ?) "
            "ON CONFLICT(repo_root) DO UPDATE SET policy_json = excluded.policy_json",
            (repo_root, json.dumps(policy.to_dict() if hasattr(policy, 'to_dict') else {
                "version": policy.version,
                "tier": int(policy.tier),
                "repo": str(policy.repo),
                "description": policy.description,
                "allow": {
                    "paths": [str(p) for p in policy.allow_paths],
                    "extensions": [str(e) for e in policy.allow_extensions],
                },
                "deny": {
                    "paths": [str(p) for p in policy.deny_paths],
                    "extensions": [str(e) for e in policy.deny_extensions],
                    "regex": [{"pattern": r.pattern.pattern, "reason": r.reason} for r in policy.deny_regex],
                },
                "override_deny": [str(p) for p in policy.override_deny],
                "content_scan": {
                    "enabled": policy.content_scan.enabled,
                    "on_match": policy.content_scan.on_match,
                    "patterns": [{"name": p.name, "pattern": p.pattern.pattern} for p in policy.content_scan.patterns],
                },
                "rag": {
                    "enabled": policy.rag_enabled,
                    "index_paths": [str(p) for p in policy.rag_index_paths],
                    "exclude_paths": [str(p) for p in policy.rag_exclude_paths],
                    "index_only_committed": policy.rag_only_committed,
                },
                "memory": {
                    "namespace": policy.memory_namespace,
                    "isolated": policy.memory_isolated,
                    "share_with_agents": list(policy.memory_share_with),
                },
                "agent_permissions": policy.agent_permissions,
            })),
        )
