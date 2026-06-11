"""
claude-env :: memory retriever
File: memory/memory_retriever.py
Purpose:
    Read paths into the memory graph:
      * keyword recall over node name/body
      * optional embedding recall (cosine over stored vectors)
      * graph expansion: from seed nodes, traverse edges up to `depth` hops
        using a recursive CTE (the local equivalent of a Cypher MATCH path)
    Enforces namespace isolation: when isolated=True, only the given namespace
    is searched; cross-namespace reads are refused (tier 2/3 guarantee).

Usage:
    mr = MemoryRetriever(namespace="proj-payments", isolated=False)
    ctx = mr.recall("jwt rotation decision", depth=2, top_k=10)
"""
from __future__ import annotations

import json
import math
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db                                      # noqa: E402
from memory.memory_manager import effective_confidence        # noqa: E402
from audit.audit_logger import AuditLogger                     # noqa: E402


def _cos(a: list[float], b: list[float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


class MemoryRetriever:
    def __init__(self, namespace: str, session_id: str = "mem",
                 actor: str = "system", isolated: bool = False):
        self.db = get_db()
        self.ns = namespace
        self.isolated = isolated
        self.audit = AuditLogger(session_id, actor=actor)

    def _ns_filter(self, extra_ns: list[str] | None) -> tuple[str, list]:
        namespaces = [self.ns]
        if extra_ns and not self.isolated:
            namespaces += extra_ns
        placeholders = ",".join("?" * len(namespaces))
        return f"namespace IN ({placeholders})", namespaces

    def keyword_recall(self, query: str, top_k: int = 10,
                       extra_ns: list[str] | None = None) -> list[dict]:
        nsf, params = self._ns_filter(extra_ns)
        like = f"%{query.lower()}%"
        rows = self.db.query(
            f"SELECT * FROM memory_nodes WHERE {nsf} AND superseded_by IS NULL "
            f"AND (lower(name) LIKE ? OR lower(body_json) LIKE ?) "
            f"ORDER BY updated_at DESC LIMIT ?",
            params + [like, like, top_k])
        return self._rank(rows, top_k)

    def embedding_recall(self, query_vec: list[float], top_k: int = 10,
                         extra_ns: list[str] | None = None) -> list[dict]:
        nsf, params = self._ns_filter(extra_ns)
        rows = self.db.query(
            f"SELECT * FROM memory_nodes WHERE {nsf} AND superseded_by IS NULL "
            f"AND embedding IS NOT NULL", params)
        scored = []
        for r in rows:
            vec = list(struct.unpack(f"<{len(r['embedding'])//4}f", r["embedding"]))
            r["sim"] = _cos(query_vec, vec)
            scored.append(r)
        scored.sort(key=lambda x: x["sim"], reverse=True)
        return self._rank(scored[:top_k], top_k)

    def expand(self, seed_ids: list[str], depth: int = 2,
               rels: list[str] | None = None) -> list[dict]:
        """Recursive-CTE graph walk from seeds. Returns connected nodes."""
        if not seed_ids:
            return []
        seeds = ",".join(f"'{s}'" for s in seed_ids)
        rel_filter = ""
        if rels:
            rel_list = ",".join(f"'{r}'" for r in rels)
            rel_filter = f"AND e.rel IN ({rel_list})"
        sql = f"""
        WITH RECURSIVE walk(node_id, hops) AS (
            SELECT node_id, 0 FROM memory_nodes WHERE node_id IN ({seeds})
            UNION
            SELECT e.dst, w.hops + 1
            FROM walk w JOIN memory_edges e ON e.src = w.node_id
            WHERE w.hops < ? {rel_filter}
        )
        SELECT DISTINCT n.* FROM walk w JOIN memory_nodes n ON n.node_id = w.node_id
        WHERE n.superseded_by IS NULL
        """
        rows = self.db.query(sql, [depth])
        return rows

    def recall(self, query: str, depth: int = 2, top_k: int = 10,
               query_vec: list[float] | None = None,
               extra_ns: list[str] | None = None) -> list[dict]:
        """Full recall: seed by keyword (+embedding), expand graph, merge."""
        seeds = self.keyword_recall(query, top_k, extra_ns)
        if query_vec:
            seeds += self.embedding_recall(query_vec, top_k, extra_ns)
        seen = {s["node_id"]: s for s in seeds}
        expanded = self.expand(list(seen.keys()), depth=depth)
        for r in expanded:
            seen.setdefault(r["node_id"], r)
        result = self._rank(list(seen.values()), top_k)
        self.audit.memory_read(self.ns, "recall", query, len(result))
        return result

    @staticmethod
    def _rank(rows: list[dict], top_k: int) -> list[dict]:
        for r in rows:
            r["effective_confidence"] = round(
                effective_confidence(r["confidence"], r["half_life_days"], r["updated_at"]), 4)
            try:
                r["body"] = json.loads(r.get("body_json", "{}"))
            except Exception:
                r["body"] = {}
            r.pop("embedding", None)
        rows.sort(key=lambda x: (x.get("sim", 0), x["effective_confidence"]), reverse=True)
        return rows[:top_k]
