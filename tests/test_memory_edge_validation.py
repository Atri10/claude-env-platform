"""Coverage for memory-graph write-path validation. Run: pytest tests/ -q

Regression for two write-side gaps in MemoryManager.add_edge():
  * `rel` was free-form text — any string was accepted despite a documented
    vocabulary, so junk relations could pollute the graph and silently defeat
    rel-filtered traversal.
  * edges could join nodes across namespaces (a write-side isolation leak) and
    were not audited, unlike every other memory write.
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _fresh_db():
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    db.get_db().apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    return db.get_db()


def _node(db, node_id, ns, name="n"):
    db.execute(
        "INSERT INTO memory_nodes(node_id,namespace,memory_type,node_kind,name,"
        "body_json,confidence,half_life_days,created_at,updated_at,last_access) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [node_id, ns, "semantic", "concept", name, "{}", 1.0, 90.0,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00", "2026-01-01T00:00:00"])


def _mgr(ns):
    from memory.memory_manager import MemoryManager
    return MemoryManager(namespace=ns, session_id="test", actor="test")


def test_add_edge_rejects_unknown_rel():
    db = _fresh_db()
    _node(db, "a1", "proj-a")
    _node(db, "a2", "proj-a")
    from memory.memory_manager import InvalidMemoryRel
    with pytest.raises(InvalidMemoryRel):
        _mgr("proj-a").add_edge("a1", "a2", "totally-made-up")


def test_add_edge_accepts_documented_rels_including_consolidates():
    db = _fresh_db()
    _node(db, "a1", "proj-a")
    _node(db, "a2", "proj-a")
    # CONSOLIDATES is emitted by the consolidator; it must be accepted even
    # though the original schema comment omitted it.
    eid = _mgr("proj-a").add_edge("a1", "a2", "CONSOLIDATES")
    assert eid.startswith("edge-")
    row = db.query_one("SELECT rel FROM memory_edges WHERE edge_id=?", (eid,))
    assert row["rel"] == "CONSOLIDATES"


def test_add_edge_rejects_cross_namespace():
    db = _fresh_db()
    _node(db, "a1", "proj-a")
    _node(db, "b1", "proj-b")
    from memory.memory_manager import CrossNamespaceEdge
    with pytest.raises(CrossNamespaceEdge):
        _mgr("proj-a").add_edge("a1", "b1", "RELATES_TO")


def test_add_edge_rejects_nonexistent_endpoint():
    db = _fresh_db()
    _node(db, "a1", "proj-a")
    from memory.memory_manager import CrossNamespaceEdge
    with pytest.raises(CrossNamespaceEdge):
        _mgr("proj-a").add_edge("a1", "ghost", "RELATES_TO")


def test_add_edge_is_audited():
    db = _fresh_db()
    _node(db, "a1", "proj-a")
    _node(db, "a2", "proj-a")
    eid = _mgr("proj-a").add_edge("a1", "a2", "DEPENDS_ON")
    # the write must land in the memory_writes projection AND the audit ledger
    mw = db.query_one(
        "SELECT operation, memory_type FROM memory_writes WHERE node_id=?", (eid,))
    assert mw is not None and mw["operation"] == "link:DEPENDS_ON"
    assert mw["memory_type"] == "edge"


def test_add_edge_keeps_audit_chain_intact():
    _fresh_db()
    from audit.audit_logger import AuditLogger
    import lib.db as db
    d = db.get_db()
    _node(d, "a1", "proj-a")
    _node(d, "a2", "proj-a")
    _mgr("proj-a").add_edge("a1", "a2", "RELATES_TO")
    ok, broken = AuditLogger("verify", actor="verify").verify_chain()
    assert ok, f"chain broke at {broken}"
