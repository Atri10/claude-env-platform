"""
Tests for the filesystem-policy MCP server (claudenv/adapters/mcp/filesystem/server.py).

The refactor into claudenv/ shipped this adapter with several bugs that only
show up when the server is actually constructed/exercised (not just
imported):
  - create_server() called the non-existent PolicyEngine.load(...) instead of
    PolicyService(config).load_engine(...).
  - create_server() called the non-existent ConfigProvider.get_audit_logger(...)
    instead of building a SqliteAuditLogger directly.
  - Several call sites read self.engine.repo.tier, but PolicyEngine exposes no
    `.repo` attribute at all (tier lives on the compiled policy).
  - create_server() passed a RepoSlug value object where SqliteAuditLogger
    expects a plain str, corrupting the audit envelope's repo field.

These tests exercise the FilesystemPolicyServer's tool implementations
directly against fakes/a real temp repo (catching the tier/policy-enforcement
bugs), and a smaller integration test drives create_server() itself end to
end against a real sqlite DB (catching the wrong-API-name bugs pyflakes can't
see, since PolicyEngine/ConfigProvider/RepoSlug are all real objects with
some method - just not the one that was being called).

pytest-asyncio is not installed in this environment (it's declared in
pyproject.toml but not present in the venv), so async handlers are driven
with a plain asyncio.run() helper rather than @pytest.mark.asyncio.
"""
from __future__ import annotations

import asyncio
import sqlite3
import subprocess

import pytest

from claudenv.adapters.mcp.filesystem.server import FilesystemPolicyServer, PolicyBlocked
from claudenv.domain.policy import PolicyEngine
from claudenv.domain.value_objects import Tier


def run(coro):
    return asyncio.run(coro)


class FakeAuditLogger:
    """Minimal IAuditLogger fake that records every call so tests can assert
    on what was audited (and, critically, what tier was reported)."""

    def __init__(self):
        self.tool_calls = []
        self.security_events = []
        self.policy_violations = []
        self.agent_actions = []

    def tool_call(self, tool, args, result_kind, duration_ms=None):
        self.tool_calls.append({"tool": tool, "args": args, "result_kind": result_kind})

    def agent_action(self, agent, action, target=None, summary=None, success=True):
        self.agent_actions.append({"agent": agent, "action": action, "target": target})

    def security_event(self, category, severity, detail, source=None):
        self.security_events.append({"category": category, "severity": severity, "detail": detail})

    def policy_violation(self, path, rule, decision, tier=None):
        self.policy_violations.append({"path": path, "rule": rule, "decision": decision, "tier": tier})


def _engine(tier=1, allow_paths=None, deny_paths=None, global_deny_paths=None, default_deny=False):
    repo_data = {
        "version": 1,
        "tier": tier,
        "repo": "smoke-repo",
        "allow": {"paths": allow_paths or []},
        "deny": {"paths": deny_paths or []},
    }
    global_data = {
        "version": 1,
        "tier": 1,
        "deny": {"paths": global_deny_paths or []},
        "tiers": {tier: {"default_deny": default_deny}},
    }
    return PolicyEngine.from_yaml(global_data, repo_data)


def _server(tmp_path, engine, audit=None):
    return FilesystemPolicyServer(
        repo_root=tmp_path,
        audit_logger=audit or FakeAuditLogger(),
        policy_engine=engine,
        session_id="test-fs",
    )


class TestReadWrite:
    def test_write_then_read_roundtrip(self, tmp_path):
        engine = _engine()
        srv = _server(tmp_path, engine)
        run(srv._do_write("hello.txt", "hi there"))
        result = run(srv._do_read("hello.txt"))
        assert "hi there" in result[0].text

    def test_path_traversal_rejected(self, tmp_path):
        engine = _engine()
        srv = _server(tmp_path, engine)
        with pytest.raises(PolicyBlocked, match="escapes repo root"):
            run(srv._do_read("../outside.txt"))


class TestPolicyEnforcement:
    """Verifies the CLAUDE.md invariant: deny always wins, tier-3 default-deny."""

    def test_tier_default_deny_blocks_unlisted_path(self, tmp_path):
        engine = _engine(tier=3, allow_paths=["allowed.txt"], default_deny=True)
        srv = _server(tmp_path, engine)
        (tmp_path / "allowed.txt").write_text("ok")
        (tmp_path / "other.txt").write_text("nope")

        # Allow-listed path passes.
        result = run(srv._do_read("allowed.txt"))
        assert "ok" in result[0].text

        # Anything not allow-listed is blocked under tier default-deny.
        with pytest.raises(PolicyBlocked, match="default-deny"):
            run(srv._do_read("other.txt"))

    def test_global_deny_beats_repo_allow(self, tmp_path):
        # Repo policy explicitly allows secrets/key.txt, but global policy
        # denies the whole secrets/ tree - global deny must still win.
        engine = _engine(allow_paths=["secrets/key.txt"], global_deny_paths=["secrets/**"])
        srv = _server(tmp_path, engine)
        (tmp_path / "secrets").mkdir()
        (tmp_path / "secrets" / "key.txt").write_text("shh")

        with pytest.raises(PolicyBlocked, match="global deny"):
            run(srv._do_read("secrets/key.txt"))

    def test_blocked_read_is_audited_with_correct_tier(self, tmp_path):
        engine = _engine(tier=3, default_deny=True)
        audit = FakeAuditLogger()
        srv = _server(tmp_path, engine, audit=audit)
        (tmp_path / "x.txt").write_text("data")

        with pytest.raises(PolicyBlocked):
            run(srv._do_read("x.txt"))

        assert len(audit.policy_violations) == 1
        # This is the regression check: self.engine.repo.tier does not exist
        # on PolicyEngine (only on CompiledPolicy); the fixed code must read
        # tier off engine.get_compiled().tier instead.
        assert audit.policy_violations[0]["tier"] == Tier.RESTRICTED


class TestScratch:
    def test_scratch_write_read_bypasses_repo_policy(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_HOME", str(tmp_path / "home"))
        # tier-3 default-deny repo policy would block any non-allow-listed
        # repo-relative path, but scratch:// paths are allow-all by design.
        engine = _engine(tier=3, default_deny=True)
        srv = _server(tmp_path, engine)
        run(srv._do_write("scratch://notes.txt", "scratch content"))
        result = run(srv._do_read("scratch://notes.txt"))
        assert "scratch content" in result[0].text

    def test_scratch_path_cannot_escape_scratch_root(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_HOME", str(tmp_path / "home"))
        engine = _engine()
        srv = _server(tmp_path, engine)
        with pytest.raises(PolicyBlocked, match="scratch path escapes"):
            run(srv._do_read("scratch://../../etc/passwd"))


class TestCreateServerWiring:
    """Integration test driving the real create_server() factory end to end:
    real ConfigProvider, real sqlite DB, real PolicyService.load_engine(). This
    is the only way to catch 'calls a method that doesn't exist on the real
    class' bugs, since pyflakes/imports can't see them and unit tests that
    construct FilesystemPolicyServer directly skip create_server() entirely."""

    @pytest.fixture
    def sql_dir(self):
        from claudenv._data import sql_dir as _sql_dir
        return _sql_dir()

    @pytest.fixture
    def env_home(self, tmp_path, monkeypatch, sql_dir):
        home = tmp_path / "home"
        (home / "config").mkdir(parents=True)
        (home / "state").mkdir(parents=True)
        db_path = home / "state" / "claude-env.db"
        for sql_file in sorted(sql_dir.glob("*.sql")):
            subprocess.run(["sqlite3", str(db_path)], input=sql_file.read_text(),
                           text=True, check=True)
        monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
        monkeypatch.setenv("CLAUDE_ENV_DSN", f"sqlite:///{db_path}")
        return home

    def test_create_server_constructs_and_serves(self, tmp_path, env_home):
        from claudenv.adapters.mcp.filesystem.server import create_server

        repo_root = tmp_path / "repo"
        repo_root.mkdir()
        (repo_root / "readme.txt").write_text("hello")

        server = create_server(repo_root, session_id="wiring-test")

        # Regression check: PolicyEngine has no .repo attribute; get_compiled()
        # is the only correct way to read the tier back off the engine.
        assert server.engine.get_compiled().tier is not None

        result = run(server._do_read("readme.txt"))
        assert "hello" in result[0].text

        # tool_call must actually have been persisted (this exercises the
        # RepoSlug-vs-str audit_logger bug: passing a RepoSlug value object
        # instead of a plain str corrupts AuditEvent.create's canonical
        # payload and crashes on str(repo)).
        conn = sqlite3.connect(str(env_home / "state" / "claude-env.db"))
        rows = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()
        assert rows[0] >= 1
