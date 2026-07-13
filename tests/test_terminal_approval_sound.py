"""Coverage for terminal.run's approval-pending notification sound
(mcp-servers/terminal/server.py). Run: pytest tests/ -q

Regression context: the approvals web UI has no desktop notification by
design (a single always-current queue page was judged less noisy than
per-request OS toasts), which means an operator away from that tab has no
way to know a command is waiting -- it just blocks silently until the
~120s timeout. _notify_pending_approval() plays a short system sound (macOS:
afplay; Linux: canberra-gtk-play, best-effort) the moment terminal.run opens
a pending approval, on by default, disabled with
CLAUDE_ENV_APPROVAL_SOUND=false -- matching the existing
CLAUDE_ENV_APPROVAL_AUTO_UI on-by-default/opt-out pattern. Playing the sound
must never affect the approval flow itself: a missing player binary, no
audio device, or any other failure is swallowed and logged, not raised.

Regression context 2 (2026-07-13): this used to shell out via a blocking
`subprocess.run(..., timeout=5)` call directly inside terminal.run's async
handler -- since the MCP server is a single-threaded asyncio event loop, that
could stall EVERY in-flight request for up to 5s per approval, before the
approval was even pollable. `_notify_pending_approval()` is now a synchronous
launcher that schedules `_notify_pending_approval_task()` as a background
asyncio task via `asyncio.create_task` and returns immediately; the actual
`afplay`/`canberra-gtk-play` invocation (via `asyncio.create_subprocess_exec`,
not `subprocess.run`) happens in that background task, off the request path.
"""
import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]


def _load_server(tmp_path, monkeypatch):
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
    mod_log.get_logger = lambda *a, **k: logging.getLogger("test-terminal-approval-sound")
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
        "_terminal_approval_sound_server", _ROOT / "mcp-servers" / "terminal" / "server.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod._audit_calls = calls
    return mod


@pytest.fixture()
def server(tmp_path, monkeypatch):
    return _load_server(tmp_path, monkeypatch)


class _FakeProcess:
    """Stand-in for the object asyncio.create_subprocess_exec() returns."""
    async def wait(self):
        return 0


def _patch_create_subprocess_exec(server, monkeypatch, calls, raise_exc=None):
    async def _fake(*a, **k):
        if raise_exc:
            raise raise_exc
        calls.append((a, k))
        return _FakeProcess()
    monkeypatch.setattr(server.asyncio, "create_subprocess_exec", _fake)


def test_sound_disabled_via_env_var_never_schedules(server, monkeypatch):
    """When disabled, _notify_pending_approval() must return before even
    scheduling the background task -- asyncio.create_task must not be called."""
    monkeypatch.setenv("CLAUDE_ENV_APPROVAL_SOUND", "false")
    scheduled = []
    monkeypatch.setattr(server.asyncio, "create_task",
                        lambda coro: scheduled.append(coro))

    async def _run():
        server._notify_pending_approval()
    asyncio.run(_run())
    assert scheduled == []


def test_sound_enabled_schedules_background_task(server, monkeypatch):
    scheduled = []
    monkeypatch.setattr(server.asyncio, "create_task",
                        lambda coro: scheduled.append(coro) or coro.close())

    async def _run():
        server._notify_pending_approval()
    asyncio.run(_run())
    assert len(scheduled) == 1


def test_notify_pending_approval_returns_without_running_loop(server):
    """Calling the sync launcher with no running event loop (asyncio.create_task
    raises RuntimeError) must not raise -- best effort only."""
    server._notify_pending_approval()   # no asyncio.run() wrapper here -> no loop


def test_sound_task_uses_afplay_on_darwin(server, monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")
    calls = []
    _patch_create_subprocess_exec(server, monkeypatch, calls)
    asyncio.run(server._notify_pending_approval_task())
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[0] == "afplay"


def test_sound_task_uses_canberra_on_linux(server, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    calls = []
    _patch_create_subprocess_exec(server, monkeypatch, calls)
    asyncio.run(server._notify_pending_approval_task())
    assert len(calls) == 1
    argv = calls[0][0]
    assert argv[0] == "canberra-gtk-play"


def test_sound_task_failure_is_swallowed_not_raised(server, monkeypatch):
    """A missing player binary or audio device must never break the approval
    flow -- this must not raise, just log and continue."""
    monkeypatch.setattr(sys, "platform", "darwin")
    _patch_create_subprocess_exec(server, monkeypatch, [],
                                  raise_exc=FileNotFoundError("no such player"))
    asyncio.run(server._notify_pending_approval_task())   # must not raise


def test_terminal_run_calls_notify_before_blocking(server, monkeypatch):
    """End-to-end: terminal.run must fire the notification as part of opening
    the approval, before/alongside the UI-ensure step -- not skipped."""
    notify_calls = []
    monkeypatch.setattr(server, "_notify_pending_approval",
                        lambda: notify_calls.append(1))
    monkeypatch.setattr(server, "_ensure_approvals_ui", lambda: None)

    async def _fake_await_decision(req_id):
        return "approved", "operator@host"
    monkeypatch.setattr(server, "_await_decision", _fake_await_decision)

    asyncio.run(server.call_tool("terminal.run", {"command": "pwd"}))
    assert len(notify_calls) == 1


def test_notify_and_ensure_ui_do_not_block_before_await_decision(server, monkeypatch):
    """Regression: terminal.run must reach _await_decision (i.e. the approval
    becomes pollable) WITHOUT waiting on notify/ensure-ui to finish. Make both
    background tasks take a while and assert call_tool still resolves quickly
    because _await_decision short-circuits immediately."""
    import time

    async def _slow_task():
        await asyncio.sleep(0.2)

    monkeypatch.setattr(server, "_notify_pending_approval",
                        lambda: asyncio.create_task(_slow_task()))
    monkeypatch.setattr(server, "_ensure_approvals_ui",
                        lambda: asyncio.create_task(_slow_task()))

    async def _fake_await_decision(req_id):
        return "approved", "operator@host"
    monkeypatch.setattr(server, "_await_decision", _fake_await_decision)

    t0 = time.monotonic()
    asyncio.run(server.call_tool("terminal.run", {"command": "pwd"}))
    assert time.monotonic() - t0 < 0.15   # well under the 0.2s the fake tasks take
