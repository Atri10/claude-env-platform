"""
claude-env :: Adapters - SQLite Persistence - Memory Repository

SQLite implementation of ``IMemoryRepository``. Persists the memory graph
(nodes + edges) with namespace isolation.
"""
from __future__ import annotations

import json

from claudenv.domain.memory import MemoryEdge, MemoryNode, MemoryType, NodeId
from claudenv.domain.value_objects import utc_now
from claudenv.ports import IMemoryRepository

from .database import SQLiteDatabase


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
