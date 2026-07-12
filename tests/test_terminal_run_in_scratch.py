"""Coverage for terminal.run's optional in_scratch argument
(mcp-servers/terminal/server.py). Run: pytest tests/ -q

Regression context: the scratch:// filesystem scheme (filesystem.read/write/
list) gives agents a disposable per-repo directory at
$CLAUDE_ENV_HOME/scratch/<repo>/, but terminal.run had no way to reach it --
_run()'s cwd always started at REPO_ROOT, and its 'cd' escape-check
specifically rejected leaving REPO_ROOT's tree. Since SCRATCH_ROOT lives
under $CLAUDE_ENV_HOME (a completely separate tree from REPO_ROOT), any
attempt to 'cd' into scratch failed with "escapes the repo root" -- there was
no way to run an (approved) command against files in scratch. terminal.run
now accepts an optional in_scratch=true argument that starts the command in
SCRATCH_ROOT instead of REPO_ROOT, with the same 'cd' escape-check applied
against that root instead (so a scratch-rooted command can move around
inside scratch but still can't 'cd' out of it). Approval is still required
either way -- this does not add any unattended/unapproved execution path.
"""
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_server(tmp_path, monkeypatch):
    """Import mcp-servers/terminal/server.py against a fresh fake repo root
    and CLAUDE_ENV_HOME, with external deps stubbed out via monkeypatch (so
    stubs are reverted per-test, not leaked across the session)."""
    calls = []

    class _FakeAudit:
        def __init__(self, *a, **k):
            pass

        def tool_call(self, *a, **k):
            calls.append(("tool_call", a, k))

        def security_event(self, *a, **k):
            calls.append(("security_event", a, k))

        def human_approval_request(self, *a, **k):
            calls.append(("human_approval_request", a, k))
            return "req-1"

    mod_audit_pkg = types.ModuleType("audit")
    mod_audit = types.ModuleType("audit.audit_logger")
    mod_audit.AuditLogger = _FakeAudit
    monkeypatch.setitem(sys.modules, "audit", mod_audit_pkg)
    monkeypatch.setitem(sys.modules, "audit.audit_logger", mod_audit)

    mod_lib_pkg = types.ModuleType("lib")
    mod_log = types.ModuleType("lib.logging_setup")
    import logging
    mod_log.get_logger = lambda *a, **k: logging.getLogger("test-terminal-scratch-arg")
    monkeypatch.setitem(sys.modules, "lib", mod_lib_pkg)
    monkeypatch.setitem(sys.modules, "lib.logging_setup", mod_log)

    mod_mcp = types.ModuleType("mcp")
    mod_mcp_server = types.ModuleType("mcp.server")
    mod_mcp_server.Server = lambda name: types.SimpleNamespace(
        list_tools=lambda: (lambda f: f), call_tool=lambda: (lambda f: f))
    mod_mcp_stdio = types.ModuleType("mcp.server.stdio")
    mod_mcp_stdio.stdio_server = None
    class _FakeTextContent:
        def __init__(self, type, text):
            self.type = type
            self.text = text

    mod_mcp_types = types.ModuleType("mcp.types")
    mod_mcp_types.TextContent = _FakeTextContent
    mod_mcp_types.Tool = None
    monkeypatch.setitem(sys.modules, "mcp", mod_mcp)
    monkeypatch.setitem(sys.modules, "mcp.server", mod_mcp_server)
    monkeypatch.setitem(sys.modules, "mcp.server.stdio", mod_mcp_stdio)
    monkeypatch.setitem(sys.modules, "mcp.types", mod_mcp_types)

    repo = tmp_path / "demo-repo"
    repo.mkdir()
    home = tmp_path / "home"
    monkeypatch.setenv("CLAUDE_ENV_REPO_ROOT", str(repo))
    monkeypatch.setenv("CLAUDE_ENV_HOME", str(home))

    spec = importlib.util.spec_from_file_location(
        "_terminal_scratch_arg_server", _ROOT / "mcp-servers" / "terminal" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._audit_calls = calls
    return mod


@pytest.fixture()
def server(tmp_path, monkeypatch):
    return _load_server(tmp_path, monkeypatch)


def test_scratch_root_matches_filesystem_policy_convention(server, tmp_path):
    expected = (tmp_path / "home" / "scratch" / "demo-repo").resolve()
    assert server.SCRATCH_ROOT == expected


def test_run_defaults_to_repo_root(server):
    out = server._run("pwd", "run_tests")
    assert out.startswith("exit=0")
    assert str(server.REPO_ROOT) in out


def test_run_in_scratch_root_starts_in_scratch_dir(server):
    server.SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    out = server._run("pwd", "run_tests", root=server.SCRATCH_ROOT)
    assert out.startswith("exit=0")
    assert str(server.SCRATCH_ROOT) in out
    assert str(server.REPO_ROOT) not in out


def test_cd_within_scratch_root_is_allowed(server):
    server.SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    (server.SCRATCH_ROOT / "sub").mkdir()
    out = server._run("cd sub && pwd", "run_tests", root=server.SCRATCH_ROOT)
    assert out.startswith("exit=0")
    assert str(server.SCRATCH_ROOT / "sub") in out


def test_cd_out_of_scratch_root_is_rejected(server):
    server.SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    out = server._run("cd .. && pwd", "run_tests", root=server.SCRATCH_ROOT)
    assert "escapes the allowed root" in out


def test_cd_from_scratch_into_repo_root_is_rejected(server):
    """The realistic escape attempt: a scratch-rooted command trying to
    reach the real repo tree via an absolute path -- must still fail, since
    SCRATCH_ROOT and REPO_ROOT are unrelated directory trees."""
    server.SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)
    out = server._run(f"cd {server.REPO_ROOT} && pwd", "run_tests",
                     root=server.SCRATCH_ROOT)
    assert "escapes the allowed root" in out


def test_terminal_run_in_scratch_starts_in_scratch_and_still_requires_approval(
        server, monkeypatch):
    """End-to-end through call_tool: in_scratch=true routes _run() at
    SCRATCH_ROOT, but the human-approval gate is completely unchanged --
    still opens a request and blocks on the same decision poll."""
    import asyncio

    server.SCRATCH_ROOT.mkdir(parents=True, exist_ok=True)

    async def _fake_await_decision(req_id):
        return "approved", "operator@host"
    monkeypatch.setattr(server, "_await_decision", _fake_await_decision)
    monkeypatch.setattr(server, "_ensure_approvals_ui", lambda: None)

    result = asyncio.run(server.call_tool("terminal.run",
        {"command": "pwd", "in_scratch": True}))
    text = result[0].text
    assert "APPROVED by operator@host" in text
    assert str(server.SCRATCH_ROOT) in text

    approval_calls = [c for c in server._audit_calls if c[0] == "human_approval_request"]
    assert len(approval_calls) == 1, "approval must still be requested for in_scratch"


def test_terminal_run_without_in_scratch_still_uses_repo_root(server, monkeypatch):
    import asyncio

    async def _fake_await_decision(req_id):
        return "approved", "operator@host"
    monkeypatch.setattr(server, "_await_decision", _fake_await_decision)
    monkeypatch.setattr(server, "_ensure_approvals_ui", lambda: None)

    result = asyncio.run(server.call_tool("terminal.run", {"command": "pwd"}))
    text = result[0].text
    assert str(server.REPO_ROOT) in text
