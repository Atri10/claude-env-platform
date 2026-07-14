"""
End-to-end construction + smoke tests for the memory_graph, documentation,
and lancedb_rag MCP servers' create_server() functions.

Each of these three create_server() implementations called methods that
never existed on ConfigProvider (config.get_memory_service(),
config.get_docs_service(), config.get_rag_service(), config.get_audit_logger())
-- IConfigProvider (claudenv/ports/config.py) never declared them, and
ConfigProvider never implemented them. Importing the modules or running
pyflakes over them doesn't catch this (they're valid attribute-access
expressions, just against objects that don't have those attributes) -- only
actually calling create_server() does. These tests build each server against
a real temporary SQLite database (with the platform's schema applied) and a
real temporary repo directory, then exercise a couple of tool handlers,
exactly the way the servers are used for real.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from claudenv._data import sql_dir as _sql_dir

_SQL_DIR = _sql_dir()


@pytest.fixture()
def env_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "state").mkdir(parents=True)
    (home / "knowledge" / "docs").mkdir(parents=True)
    (home / "knowledge" / "lancedb").mkdir(parents=True)

    from claudenv.adapters.persistence.sqlite import SQLiteDatabase
    db = SQLiteDatabase(f"sqlite:///{home}/state/claude-env.db")
    for f in sorted(_SQL_DIR.glob("*.sql")):
        db._conn.executescript(f.read_text())
    db.close()

    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    monkeypatch.setenv("EMBED_BACKEND", "dummy")
    monkeypatch.delenv("CLAUDE_ENV_DSN", raising=False)

    import claudenv.adapters.config as config_module
    config_module._config_provider = None  # force a fresh ConfigProvider to pick up env vars
    return home


@pytest.fixture()
def repo_root(tmp_path):
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "README.md").write_text("# Test Repo\n")
    (root / "src" / "app.py").write_text("class Widget:\n    pass\n")
    return root


def test_memory_graph_server_construction_and_roundtrip(env_home, repo_root, monkeypatch):
    monkeypatch.setenv("CLAUDE_ENV_REPO_ROOT", str(repo_root))
    from claudenv.adapters.mcp.memory_graph.server import create_server

    async def run():
        server = create_server("test-repo")
        stored = await server._do_store({"content": "a decision", "type": "semantic",
                                          "kind": "entity", "name": "n1"})
        assert "Stored:" in stored[0].text
        node_id = stored[0].text.split(": ")[1]

        retrieved = await server._do_retrieve({"node_id": node_id})
        assert "n1" in retrieved[0].text

        assert server.audit.verify_chain().ok

    asyncio.run(run())


def test_documentation_server_construction_and_roundtrip(env_home, repo_root, monkeypatch):
    monkeypatch.setenv("CLAUDE_ENV_REPO_ROOT", str(repo_root))
    from claudenv.adapters.mcp.documentation.server import create_server

    async def run():
        server = create_server("test-repo")
        readme = await server._do_get_readme()
        assert "Test Repo" in readme[0].text

        refs = await server._do_find_reference({"query": "widget", "kind": "any"})
        assert "Widget" in refs[0].text

        assert server.audit.verify_chain().ok

    asyncio.run(run())


def test_lancedb_rag_server_construction_and_roundtrip(env_home, repo_root, monkeypatch):
    pytest.importorskip("lancedb")
    monkeypatch.setenv("CLAUDE_ENV_REPO_ROOT", str(repo_root))
    from claudenv.adapters.mcp.lancedb_rag.server import create_server

    async def run():
        server = create_server("test-repo")
        index_result = await server._do_index({"force_full": True})
        assert "Indexing complete" in index_result[0].text

        status = await server._do_status()
        assert "test-repo" in status[0].text

        search_result = await server._do_search({"query": "widget"})
        assert "retrieved_context" in search_result[0].text

        assert server.audit.verify_chain().ok

    asyncio.run(run())


def test_memory_link_accepts_lowercase_relation_from_tool_schema(env_home, repo_root, monkeypatch):
    """memory.link's own inputSchema advertises lowercase relation names
    (e.g. "relates_to"), but EdgeRelation's enum values are UPPER_SNAKE_CASE
    (RELATES_TO) -- so every schema-valid call used to raise ValueError."""
    monkeypatch.setenv("CLAUDE_ENV_REPO_ROOT", str(repo_root))
    from claudenv.adapters.mcp.memory_graph.server import create_server

    async def run():
        server = create_server("test-repo")
        a = await server._do_store({"content": "a", "type": "semantic", "kind": "entity", "name": "a"})
        b = await server._do_store({"content": "b", "type": "semantic", "kind": "entity", "name": "b"})
        node_a = a[0].text.split(": ")[1]
        node_b = b[0].text.split(": ")[1]

        linked = await server._do_link({"source": node_a, "target": node_b, "relation": "relates_to"})
        assert "Linked:" in linked[0].text

        expanded = await server._do_expand({"seed_ids": [node_a], "relations": ["relates_to"]})
        assert node_b in expanded[0].text

    asyncio.run(run())
