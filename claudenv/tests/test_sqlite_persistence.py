"""
Tests for the SQLite adapters (claudenv/adapters/persistence/sqlite.py and
claudenv/adapters/audit.py) against a real temporary SQLite database with the
platform's actual schema applied. These specifically cover runtime-only bugs
that a fake IDatabase/IMemoryRepository can't surface, because they live in
the adapter's own SQL binding / attribute-name code:

  - SQLiteMemoryRepository.insert_edge() referenced edge.rel (MemoryEdge has
    no such attribute -- only .relation), so every edge write raised
    AttributeError.
  - SQLiteMemoryRepository.expand_graph() bound NodeId objects directly as
    SQL parameters (sqlite3 can't bind an arbitrary object), so any recall
    that reached the graph-expansion step raised sqlite3.ProgrammingError.
  - SqliteAuditLogger double-wrapped an already-constructed RepoSlug via
    RepoSlug.from_string(self._repo), which stores the RepoSlug instance
    itself as .value, breaking str(repo) (and the SQL bind) on every
    audited call for every MCP server that constructs it the same way
    terminal/server.py's create_server() does (passing a RepoSlug, not a
    plain string).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.persistence.sqlite import SQLiteDatabase, SQLiteMemoryRepository
from claudenv.domain.memory import EdgeRelation, MemoryNode, MemoryType, NodeKind
from claudenv.domain.value_objects import RepoSlug, SessionId, Tier
from claudenv._data import sql_dir as _sql_dir

_SQL_DIR = _sql_dir()


@pytest.fixture()
def db(tmp_path) -> SQLiteDatabase:
    dsn = f"sqlite:///{tmp_path}/test.db"
    database = SQLiteDatabase(dsn)
    for f in sorted(_SQL_DIR.glob("*.sql")):
        database._conn.executescript(f.read_text())
    return database


class TestMemoryEdgePersistence:
    def test_insert_edge_uses_relation_not_rel_attribute(self, db):
        repo = SQLiteMemoryRepository(db)
        a = MemoryNode.create("ns", MemoryType.SEMANTIC, NodeKind.ENTITY, "a", {})
        b = MemoryNode.create("ns", MemoryType.SEMANTIC, NodeKind.ENTITY, "b", {})
        repo.insert_node(a)
        repo.insert_node(b)
        from claudenv.domain.memory import MemoryEdge
        edge = MemoryEdge.create("ns", a.node_id, b.node_id, EdgeRelation.RELATES_TO)

        # Used to raise AttributeError: 'MemoryEdge' object has no attribute 'rel'
        repo.insert_edge(edge)

        fetched = repo.get_edges(src=a.node_id)
        assert len(fetched) == 1
        assert fetched[0].relation == EdgeRelation.RELATES_TO
        assert fetched[0].dst == b.node_id

    def test_expand_graph_binds_node_ids_as_strings(self, db):
        repo = SQLiteMemoryRepository(db)
        a = MemoryNode.create("ns", MemoryType.SEMANTIC, NodeKind.ENTITY, "a", {})
        b = MemoryNode.create("ns", MemoryType.SEMANTIC, NodeKind.ENTITY, "b", {})
        repo.insert_node(a)
        repo.insert_node(b)
        from claudenv.domain.memory import MemoryEdge
        repo.insert_edge(MemoryEdge.create("ns", a.node_id, b.node_id, EdgeRelation.RELATES_TO))

        # Used to raise sqlite3.ProgrammingError: type 'NodeId' is not supported
        expanded = repo.expand_graph(
            seed_ids=[a.node_id], depth=2, relations=None,
            namespace="ns", extra_namespaces=None,
        )
        node_ids = {n.node_id for n in expanded}
        assert a.node_id in node_ids
        assert b.node_id in node_ids


class TestAuditLoggerRepoNormalization:
    def test_accepts_already_constructed_reposlug(self, db):
        # Every create_server() in this codebase passes an already-built
        # RepoSlug (mirroring terminal/server.py). This used to break
        # because _append() re-wrapped it: RepoSlug.from_string(a_reposlug)
        # stores the RepoSlug itself as .value, and str(repo) then raised
        # TypeError: __str__ returned non-string (type RepoSlug).
        logger = SqliteAuditLogger(
            db=db, session_id=SessionId.from_string("s"), actor="a",
            repo=RepoSlug.from_string("my-repo"), tier=Tier.INTERNAL,
        )
        event_id = logger.tool_call(tool="x", args={}, result_kind="ok")
        assert event_id is not None

        row = db.query_one("SELECT repo FROM audit_events WHERE event_id = ?",
                            (int(str(event_id).split("-")[1]),))
        assert row["repo"] == "my-repo"

    def test_accepts_plain_string_repo(self, db):
        logger = SqliteAuditLogger(
            db=db, session_id=SessionId.from_string("s"), actor="a",
            repo="my-repo", tier=Tier.INTERNAL,
        )
        logger.tool_call(tool="x", args={}, result_kind="ok")
        result = logger.verify_chain()
        assert result.ok
        assert result.total_events == 1
