"""
Incident-mode fail-closed.

Requirement: the policy engine must deny *any* evaluation while the INCIDENT
marker is present. Because the filesystem MCP server and the RAG indexer route
every path through ``PolicyEngine.evaluate_path``, arming incident mode fails
them closed too. This module proves the engine AND the filesystem server both
deny, and that ``claude-env incident on`` denies in-flight approvals.
"""
from __future__ import annotations

import asyncio

import pytest
from click.testing import CliRunner

import claudenv.di as di
from claudenv._data import sql_dir as _sql_dir
from claudenv.adapters.incident import is_incident_active
from claudenv.adapters.mcp.filesystem.server import FilesystemPolicyServer, PolicyBlocked
from claudenv.cli import cli
from claudenv.domain.incident import (
    IncidentState,
    clear_incident,
    write_incident,
)
from claudenv.domain.policy import PolicyEngine
from claudenv.ports.approval import GateVerdict

run = asyncio.run


class FakeAudit:
    """Minimal IAuditLogger that records nothing but satisfies the server."""

    def tool_call(self, *a, **k):
        pass

    def agent_action(self, *a, **k):
        pass

    def security_event(self, *a, **k):
        pass

    def policy_violation(self, *a, **k):
        pass


def _engine(tier: int = 1, deny: list[str] | None = None, incident_active: bool | None = None) -> PolicyEngine:
    repo_data = {
        "version": 1, "tier": tier, "repo": "smoke-repo",
        "deny": {"paths": deny or []},
    }
    global_data = {
        "version": 1, "tier": 1, "deny": {"paths": []},
        "tiers": {tier: {"default_deny": False}},
    }
    if incident_active is None:
        incident_active = is_incident_active()
    return PolicyEngine.from_yaml(global_data, repo_data, incident_active=incident_active)


@pytest.fixture()
def incident_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
    write_incident(reason="security drill", by="operator", home=home)
    assert is_incident_active(home=home)
    yield home
    clear_incident(home=home)


class TestEngineFailClosed:
    def test_engine_denies_normally_allowed_path_during_incident(self, incident_home):
        # A path that policy would ALLOW is still denied because of incident mode.
        engine = _engine(deny=[])
        decision = engine.evaluate_path("src/app.py")
        assert decision.action.value == "block"
        assert decision.rule == "INCIDENT"
        assert "incident" in decision.reason.lower()

    def test_engine_denies_already_blocked_path_during_incident(self, incident_home):
        engine = _engine(deny=["secret.py"])
        decision = engine.evaluate_path("secret.py")
        assert decision.action.value == "block"
        assert decision.rule == "INCIDENT"

    def test_engine_normal_when_no_incident(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CLAUDE_ENV_HOME", str(tmp_path / "no-marker"))
        engine = _engine(deny=["secret.py"])
        assert engine.evaluate_path("src/app.py").is_allowed
        assert not engine.evaluate_path("secret.py").is_allowed


class TestFilesystemServerFailClosed:
    def _server(self, tmp_path, engine):
        return FilesystemPolicyServer(
            repo_root=tmp_path, audit_logger=FakeAudit(),
            policy_engine=engine, session_id="test-fs",
        )

    def test_fs_read_denied_during_incident(self, incident_home, tmp_path):
        srv = self._server(tmp_path, _engine(deny=[]))
        with pytest.raises(PolicyBlocked):
            run(srv._do_read("src/app.py"))

    def test_fs_write_denied_during_incident(self, incident_home, tmp_path):
        srv = self._server(tmp_path, _engine(deny=[]))
        with pytest.raises(PolicyBlocked):
            run(srv._do_write("src/app.py", "malicious"))

    def test_fs_list_denied_during_incident(self, incident_home, tmp_path):
        srv = self._server(tmp_path, _engine(deny=[]))
        # list walks the tree and evaluates each child; incident mode fails it.
        with pytest.raises(PolicyBlocked):
            run(srv._do_list("."))

    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "state").mkdir(parents=True)
        (home / "config").mkdir(parents=True)
        dsn = f"sqlite:///{home}/state/claude-env.db"
        monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
        monkeypatch.setenv("CLAUDE_ENV_DSN", dsn)
        monkeypatch.setenv("EMBED_BACKEND", "dummy")
        monkeypatch.setenv("RERANKER_BACKEND", "noop")
        from claudenv.adapters.persistence import SQLiteDatabase
        db = SQLiteDatabase(dsn)
        for f in sorted(_sql_dir().glob("*.sql")):
            db._conn.executescript(f.read_text())
        db.close()
        di.reset_container()
        yield home
        di.reset_container()


class TestIncidentCliDeniesApprovals:
    """``incident on`` must fail-closed by denying any pending human_approvals."""

    @pytest.fixture()
    def env(self, tmp_path, monkeypatch):
        home = tmp_path / "home"
        (home / "state").mkdir(parents=True)
        (home / "config").mkdir(parents=True)
        dsn = f"sqlite:///{home}/state/claude-env.db"
        monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))
        monkeypatch.setenv("CLAUDE_ENV_DSN", dsn)
        from claudenv.adapters.persistence import SQLiteDatabase
        db = SQLiteDatabase(dsn)
        for f in sorted(_sql_dir().glob("*.sql")):
            db._conn.executescript(f.read_text())
        db.close()
        di.reset_container()
        yield home
        di.reset_container()

    def test_incident_on_denies_pending_approvals(self, env):
        from claudenv.adapters.audit import SqliteAuditLogger
        from claudenv.adapters.persistence import SQLiteDatabase
        from claudenv.application.approval import ApprovalGate
        from claudenv.domain.value_objects import SessionId, Tier

        dsn = f"sqlite:///{env}/state/claude-env.db"
        db = SQLiteDatabase(dsn)
        audit = SqliteAuditLogger(
            db, SessionId.from_string("test"), actor="test", repo="repo", tier=Tier.RESTRICTED,
        )
        gate = ApprovalGate(audit, db)
        rid = gate.open(GateVerdict(
            required=True, agent="terminal",
            action="terminal.run: rm -rf /", target=None, tier=3, reasons=["destructive"],
        ))
        assert any(r["request_id"] == rid for r in gate.list_open())

        result = CliRunner().invoke(
            cli, ["incident", "on", "--reason", "breach"], catch_exceptions=False,
        )
        assert result.exit_code == 0
        assert "ON" in result.output

        # Pending approval is now resolved as denied, recorded as INCIDENT.
        assert gate.list_open() == []
        recent = [r for r in gate.list_recent(limit=10) if r["request_id"] == rid]
        assert recent, "approval should have been resolved"
        assert recent[0]["decision"] == "denied"
        assert recent[0]["decided_by"] == "INCIDENT"

    def test_incident_status_reads_shared_marker(self, env):
        CliRunner().invoke(
            cli, ["incident", "on", "--reason", "drill", "--by", "alice"],
            catch_exceptions=False,
        )
        state = IncidentState.read(home=env)
        assert state.active
        assert state.reason == "drill"
        assert state.by == "alice"

        CliRunner().invoke(cli, ["incident", "off"], catch_exceptions=False)
        assert not is_incident_active(home=env)
        assert not IncidentState.read(home=env).active
