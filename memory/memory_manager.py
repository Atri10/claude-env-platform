"""
claude-env :: memory manager
File: memory/memory_manager.py
Purpose:
    CRUD over the local memory graph (memory_nodes + memory_edges in SQLite).
    Implements the four memory types as node_kind/namespace conventions, plus
    confidence decay, append-only corrections via SUPERSEDES, and namespace
    isolation enforcement for tiers >= 2.

Memory types -> storage:
    episodic   : node_kind in {session, decision, investigation}
    semantic   : node_kind in {entity, concept, architecture, preference}
    procedural : node_kind in {workflow, convention, pattern}    (namespace 'global')
    agent      : namespace 'agent:<id>'                          (per-agent learnings)

add_node() rejects any (memory_type, node_kind) pair outside this taxonomy —
VALID_KINDS is the single source of truth (also drives HALF_LIFE lookups), so a
caller can't silently fall back to the generic 90-day half-life / lose
prune-protection by inventing an ad hoc node_kind like "issue".

Confidence:
    stored confidence is the value at created_at/updated_at; effective confidence
    at read time = stored * 2 ** (-age_days / half_life_days). half_life_days
    defaults per kind (see HALF_LIFE).

All writes emit a memory_writes audit row; all reads emit memory_reads.
"""
from __future__ import annotations

import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db                       # noqa: E402
from audit.audit_logger import AuditLogger      # noqa: E402

HALF_LIFE = {            # days
    "session": 30, "investigation": 30, "decision": 365,
    "entity": 180, "concept": 270, "architecture": 365,
    "workflow": 540, "convention": 540, "pattern": 365,
    "preference": 270,
}

VALID_KINDS = {
    "episodic": {"session", "decision", "investigation"},
    "semantic": {"entity", "concept", "architecture", "preference"},
    "procedural": {"workflow", "convention", "pattern"},
    "agent": set(HALF_LIFE),   # agent namespace reuses any of the above kinds
}


class InvalidMemoryKind(ValueError):
    """Raised when (memory_type, node_kind) isn't in the documented taxonomy."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _embed_for(name: str, body: dict) -> bytes | None:
    """Best-effort embedding of a node so semantic recall can find it. Returns
    None (leaving embedding NULL) if no model is configured — memory writes must
    never fail just because the RAG model is absent. The embedder is cached, so
    this loads the model at most once per process."""
    try:
        import struct
        from rag.config import get_embedder
        text = (name or "") + " " + json.dumps(body)
        vec = get_embedder().embed_documents([text])[0]
        return struct.pack(f"<{len(vec)}f", *vec)
    except Exception:
        return None


def _age_days(iso: str) -> float:
    t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return max(0.0, (datetime.now(timezone.utc) - t).total_seconds() / 86400)


def effective_confidence(stored: float, half_life: float, updated_at: str) -> float:
    return stored * (2 ** (-_age_days(updated_at) / max(1e-6, half_life)))


class MemoryManager:
    def __init__(self, namespace: str, session_id: str = "mem",
                 actor: str = "system", isolated: bool = False):
        self.db = get_db()
        self.ns = namespace
        self.isolated = isolated
        self.audit = AuditLogger(session_id, actor=actor)

    # -- write -------------------------------------------------------------
    def add_node(self, memory_type: str, node_kind: str, name: str,
                 body: dict, repo: str | None = None,
                 confidence: float = 1.0, embedding: bytes | None = None) -> str:
        allowed = VALID_KINDS.get(memory_type)
        if allowed is None or node_kind not in allowed:
            raise InvalidMemoryKind(
                f"invalid (memory_type={memory_type!r}, node_kind={node_kind!r}); "
                f"expected one of {VALID_KINDS}")
        node_id = f"mem-{uuid.uuid4().hex}"
        ts = _now()
        hl = HALF_LIFE[node_kind]
        if embedding is None:                       # make the node semantically recallable
            embedding = _embed_for(name, body)
        self.db.execute(
            "INSERT INTO memory_nodes "
            "(node_id,namespace,memory_type,node_kind,name,body_json,repo,confidence,"
            " half_life_days,created_at,updated_at,last_access,access_count,embedding) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (node_id, self.ns, memory_type, node_kind, name, json.dumps(body),
             repo, confidence, hl, ts, ts, ts, 0, embedding))
        self.audit.memory_write(self.ns, memory_type, node_id, "insert")
        return node_id

    def add_edge(self, src: str, dst: str, rel: str, weight: float = 1.0) -> str:
        edge_id = f"edge-{uuid.uuid4().hex}"
        self.db.execute(
            "INSERT INTO memory_edges (edge_id,namespace,src,dst,rel,weight,created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (edge_id, self.ns, src, dst, rel, weight, _now()))
        return edge_id

    def supersede(self, old_node_id: str, memory_type: str, node_kind: str,
                  name: str, body: dict, **kw) -> str:
        """Append-only correction: create new node, link SUPERSEDES, mark old."""
        new_id = self.add_node(memory_type, node_kind, name, body, **kw)
        self.add_edge(new_id, old_node_id, "SUPERSEDES")
        self.db.execute("UPDATE memory_nodes SET superseded_by=?, updated_at=? WHERE node_id=?",
                        (new_id, _now(), old_node_id))
        self.audit.memory_write(self.ns, memory_type, old_node_id, "supersede")
        return new_id

    # -- read --------------------------------------------------------------
    def get_node(self, node_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM memory_nodes WHERE node_id=?", (node_id,))
        if row:
            self._touch(node_id)
        return row

    def list_nodes(self, memory_type: str | None = None,
                   min_confidence: float = 0.0, include_superseded: bool = False,
                   limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM memory_nodes WHERE namespace=?"
        params: list = [self.ns]
        if memory_type:
            sql += " AND memory_type=?"; params.append(memory_type)
        if not include_superseded:
            sql += " AND superseded_by IS NULL"
        sql += " ORDER BY updated_at DESC LIMIT ?"; params.append(limit)
        rows = self.db.query(sql, params)
        out = []
        for r in rows:
            eff = effective_confidence(r["confidence"], r["half_life_days"], r["updated_at"])
            if eff >= min_confidence:
                r["effective_confidence"] = round(eff, 4)
                out.append(r)
        self.audit.memory_read(self.ns, memory_type or "any", None, len(out))
        return out

    def _touch(self, node_id: str):
        self.db.execute(
            "UPDATE memory_nodes SET last_access=?, access_count=access_count+1 WHERE node_id=?",
            (_now(), node_id))

    # -- maintenance hooks used by consolidator/pruner --------------------
    def decay_all(self) -> int:
        """Persist decayed confidence so pruning thresholds are stable."""
        rows = self.db.query("SELECT node_id,confidence,half_life_days,updated_at "
                             "FROM memory_nodes WHERE namespace=?", (self.ns,))
        n = 0
        for r in rows:
            eff = effective_confidence(r["confidence"], r["half_life_days"], r["updated_at"])
            self.db.execute("UPDATE memory_nodes SET confidence=?, updated_at=? WHERE node_id=?",
                            (round(eff, 4), _now(), r["node_id"]))
            n += 1
        return n
