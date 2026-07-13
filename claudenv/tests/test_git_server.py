"""
Tests for the git MCP server (claudenv/adapters/mcp/git/server.py).

The refactor into claudenv/ shipped this adapter with several bugs:
  - _allow_git_operation() (the deny-list/allow-list/default-deny check) was
    defined but never called from call_tool() - every git.* tool ran
    unconditionally regardless of DENIED_COMMANDS/ALLOWED_*. This silently
    dropped the server's entire policy-enforcement feature.
  - call_tool() was a plain async method on GitServer, never registered with
    `@self.server.call_tool()`. The real mcp.server.Server object therefore
    had no call_tool handler at all: list_tools() worked, but every
    "tools/call" request would fail to dispatch over stdio.
  - _run_git() had no error handling, so a missing git binary or a timeout
    would raise out of the request handler instead of returning an ERROR
    result (the pre-refactor implementation caught both).
  - create_server() called the non-existent PolicyEngine.load(...), the
    non-existent ConfigProvider.get_audit_logger(...), and read
    policy_engine.repo.tier (PolicyEngine has no .repo attribute) - the same
    three bugs found in the filesystem-policy server's create_server().
  - create_server() passed a RepoSlug value object where SqliteAuditLogger
    expects a plain str, corrupting the audit envelope's repo field.

pytest-asyncio is not installed in this environment, so async handlers are
driven with a plain asyncio.run() helper.
"""
from __future__ import annotations

import asyncio
import subprocess
import sqlite3
from pathlib import Path

import pytest

from claudenv.adapters.mcp.git.server import GitServer
from claudenv.domain.policy import PolicyEngine
from mcp.types import CallToolRequest, CallToolRequestParams


def run(coro):
    return asyncio.run(coro)


class FakeAuditLogger:
    def __init__(self):
        self.tool_calls = []
        self.security_events = []

    def tool_call(self, tool, args, result_kind, duration_ms=None):
        self.tool_calls.append({"tool": tool, "args": args, "result_kind": result_kind})

    def security_event(self, category, severity, detail, source=None):
        self.security_events.append({"category": category, "severity": severity, "detail": detail})


def _engine():
    return PolicyEngine.from_yaml(
        {"version": 1, "tier": 1},
        {"version": 1, "tier": 1, "repo": "smoke-repo"},
    )


def _git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    (repo / "a.txt").write_text("hi\n")
    subprocess.run(["git", "add", "a.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _server(repo, audit=None):
    return GitServer(repo_root=repo, audit_logger=audit or FakeAuditLogger(),
                      policy_engine=_engine(), session_id="test-git")


class TestAllowGitOperation:
    """Unit coverage for the deny-list/allow-list/default-deny check itself."""

    def test_denied_command_rejected(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        assert srv._allow_git_operation("reset --hard") is False
        assert srv._allow_git_operation("push origin main") is False

    def test_allowed_read_command_permitted(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        assert srv._allow_git_operation("status") is True
        assert srv._allow_git_operation("log --oneline -5") is True

    def test_unknown_command_default_denied(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        assert srv._allow_git_operation("bisect start") is False


class TestPolicyEnforcementWiring:
    """Regression coverage for _allow_git_operation being dead code: every
    call into _do_call must actually consult it before running git."""

    def test_denied_operation_is_blocked_and_never_runs_git(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        srv._allow_git_operation = lambda cmd: False

        def _boom(args):
            raise AssertionError("_run_git must not be called when policy denies the operation")
        srv._run_git = _boom

        result = run(srv._do_call("git.status", {}))
        assert "BLOCKED" in result[0].text

    def test_allowed_operation_runs_git(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        result = run(srv._do_call("git.status", {}))
        assert "ERROR" not in result[0].text

    def test_denied_operation_is_audited(self, tmp_path):
        audit = FakeAuditLogger()
        srv = _server(_git_repo(tmp_path), audit=audit)
        srv._allow_git_operation = lambda cmd: False
        run(srv._do_call("git.status", {}))
        assert any(tc["result_kind"] == "blocked" for tc in audit.tool_calls)


class TestGitToolHandlers:
    def test_status(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        result = run(srv._do_call("git.status", {}))
        assert "nothing to commit" in result[0].text

    def test_log(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        result = run(srv._do_call("git.log", {"limit": 5}))
        assert "init" in result[0].text

    def test_add_and_commit(self, tmp_path):
        repo = _git_repo(tmp_path)
        (repo / "b.txt").write_text("new file\n")
        srv = _server(repo)
        run(srv._do_call("git.add", {"paths": ["b.txt"]}))
        result = run(srv._do_call("git.commit", {"message": "add b"}))
        assert "ERROR" not in result[0].text

    def test_commit_without_message_rejected(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        result = run(srv._do_call("git.commit", {}))
        assert "ERROR" in result[0].text

    def test_unknown_tool(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        result = run(srv._do_call("git.push", {}))
        assert "unknown tool" in result[0].text


class TestRunGitErrorHandling:
    """Regression coverage: _run_git previously had no try/except at all, so
    a missing git binary or a timeout would raise out of the request handler
    instead of returning an ERROR result."""

    def test_missing_git_binary_returns_error_text(self, tmp_path):
        srv = _server(_git_repo(tmp_path))

        def _missing(args):
            raise FileNotFoundError("git not found")
        srv._run_git = _missing

        result = run(srv._do_call("git.status", {}))
        assert "ERROR" in result[0].text
        assert "not found" in result[0].text.lower()

    def test_timeout_returns_error_text(self, tmp_path):
        srv = _server(_git_repo(tmp_path))

        def _timeout(args):
            raise subprocess.TimeoutExpired(cmd="git", timeout=30)
        srv._run_git = _timeout

        result = run(srv._do_call("git.status", {}))
        assert "ERROR" in result[0].text
        assert "timed out" in result[0].text.lower()


class TestCallToolIsRegisteredOnTheMcpServer:
    """Regression coverage: call_tool was never wired up with
    @self.server.call_tool(), so the real mcp.server.Server object had no
    handler for CallToolRequest at all."""

    def test_call_tool_handler_is_registered(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        assert CallToolRequest in srv.server.request_handlers

    def test_call_tool_dispatches_through_the_real_mcp_handler(self, tmp_path):
        srv = _server(_git_repo(tmp_path))
        handler = srv.server.request_handlers[CallToolRequest]
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name="git.status", arguments={}),
        )
        result = run(handler(req))
        assert "nothing to commit" in result.root.content[0].text


class TestCreateServerWiring:
    """Integration test driving the real create_server() factory end to end."""

    @pytest.fixture
    def sql_dir(self):
        return Path(__file__).resolve().parents[2] / "sql"

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
        from claudenv.adapters.mcp.git.server import create_server

        repo = _git_repo(tmp_path)
        server = create_server(repo, session_id="wiring-test")

        assert server.engine.get_compiled().tier is not None

        result = run(server._do_call("git.status", {}))
        assert "nothing to commit" in result[0].text

        conn = sqlite3.connect(str(env_home / "state" / "claude-env.db"))
        rows = conn.execute("SELECT COUNT(*) FROM tool_calls").fetchone()
        assert rows[0] >= 1
