"""Coverage for memory graph namespace isolation. Run: pytest tests/ -q

Regression for the isolation leak: MemoryRetriever.expand() walked the graph
with no namespace filter, so a seed in namespace A could hop across an edge into
namespace B and return B's nodes — breaking the tier-2/3 isolation guarantee the
class documents.
"""
import importlib
import os
import sys
import tempfile
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))


def _fresh_db():
    """Point lib.db at a fresh temp sqlite and reset its singleton."""
    dsn = "sqlite:///" + tempfile.mktemp(suffix=".db")
    os.environ["CLAUDE_ENV_DSN"] = dsn
    import lib.db as db
    importlib.reload(db)
    db.get_db().apply_schema(str(_ROOT / "sql" / "001_schema.sql"))
    return db.get_db()


def _node(db, node_id, ns, name):
    db.execute(
        "INSERT INTO memory_nodes(node_id,namespace,memory_type,node_kind,name,"
        "body_json,confidence,half_life_days,created_at,updated_at,last_access) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        [node_id, ns, "semantic", "concept", name, "{}", 1.0, 90.0,
         "2026-01-01T00:00:00", "2026-01-01T00:00:00", "2026-01-01T00:00:00"])


def _edge(db, edge_id, ns, src, dst):
    db.execute(
        "INSERT INTO memory_edges(edge_id,namespace,src,dst,rel,weight,created_at)"
        " VALUES(?,?,?,?,?,?,?)",
        [edge_id, ns, src, dst, "RELATES_TO", 1.0, "2026-01-01T00:00:00"])


def test_expand_does_not_cross_namespaces():
    db = _fresh_db()
    # A: seed -> A2 (in-ns) ; A2 -> B1 (cross-ns edge, must NOT be followed)
    _node(db, "a1", "proj-a", "seed")
    _node(db, "a2", "proj-a", "neighbor")
    _node(db, "b1", "proj-b", "other-repo-secret")
    _edge(db, "e1", "proj-a", "a1", "a2")
    _edge(db, "e2", "proj-a", "a2", "b1")   # edge stored under A but points at B

    from memory.memory_retriever import MemoryRetriever
    mr = MemoryRetriever(namespace="proj-a", isolated=True)
    ids = {r["node_id"] for r in mr.expand(["a1"], depth=3)}
    assert "a2" in ids                      # same-namespace neighbor reached
    assert "b1" not in ids                  # cross-namespace node NOT leaked


def test_expand_extra_ns_allowed_when_not_isolated():
    db = _fresh_db()
    _node(db, "a1", "proj-a", "seed")
    _node(db, "g1", "global", "shared")
    _edge(db, "e1", "proj-a", "a1", "g1")

    from memory.memory_retriever import MemoryRetriever
    mr = MemoryRetriever(namespace="proj-a", isolated=False)
    ids = {r["node_id"] for r in mr.expand(["a1"], depth=2, extra_ns=["global"])}
    assert "g1" in ids                      # explicitly-shared namespace reachable
