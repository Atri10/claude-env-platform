#!/usr/bin/env python3
"""
terminal MCP server :: constrained, allow-listed command execution.

There is no general shell here. Only three classes of read-only / non-mutating
commands run automatically, each mapped to a vetted argv template:

  terminal.run_tests       -> the repo's configured test command
  terminal.run_benchmarks  -> the repo's configured benchmark command
  terminal.run_audit       -> the repo's configured security-audit command

Commands run with:
  * a hard timeout
  * the repo root as cwd (no escaping)
  * a scrubbed environment (no inherited secrets)
  * shell=False, argv lists only (no shell interpolation, no pipes)

Anything state-mutating maps to `terminal.run`, which opens a human approval,
surfaces the approvals web UI, and BLOCKS until the operator approves or denies.
On approval the command runs under the same sandbox (argv-only via shlex, no
shell/pipes, scrubbed env, repo-root cwd, timeout); on denial/timeout it does not.
There is no `terminal.exec_unrestricted` tool at all.

Configuration: each command must be set per-repo in `${repo}/.claude/commands.json`.
An unconfigured command is NOT run — it returns a directive telling the operator to
configure it (so we never run a toolchain-wrong default like pytest on a Go repo).
`claude-env onboard` auto-writes commands.json for single-toolchain repos. stdio
server. Requires: pip install mcp
"""
from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (_HOME, _HOME / "audit", _HOME / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from audit.audit_logger import AuditLogger  # noqa: E402
from lib.logging_setup import get_logger  # noqa: E402

# stderr-safe (never stdout — stdio protocol channel); also lands in
# logs/mcp-terminal.log.
_log = get_logger("mcp-terminal", stderr=True)

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    sys.stderr.write("terminal: the 'mcp' package is required (pip install mcp)\n")
    raise

REPO_ROOT = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-terminal")
TIMEOUT_S = int(os.environ.get("CLAUDE_ENV_CMD_TIMEOUT", "600"))
# Approval flow: how long terminal.run blocks waiting for a human decision, how
# often it polls, and the approvals-UI port to auto-open.
_WAIT_S = int(os.environ.get("CLAUDE_ENV_APPROVAL_WAIT_S", "120"))
_POLL_S = 2
# CLAUDE_ENV_APPROVAL_PORT (if set) is a *preferred* port hint passed to the
# approvals UI; the actual port is discovered from the service registry.

# Suggested commands, surfaced in the "not configured" hint. NOT auto-run —
# a command only executes when set explicitly in .claude/commands.json.
_DEFAULTS = {
    "run_tests": "pytest -q",
    "run_benchmarks": "pytest -q --benchmark-only",
    "run_audit": "pip-audit",
}

# Environment variables that may pass through; everything else is stripped.
_ENV_ALLOW = {"PATH", "HOME", "LANG", "LC_ALL", "TMPDIR", "VIRTUAL_ENV", "PWD"}

_audit = AuditLogger(session_id=SESSION_ID, actor="terminal-mcp",
                     repo=REPO_ROOT.name)
server = Server("terminal")


def _load_commands() -> tuple[dict, set]:
    """Return (commands, explicitly_configured_keys). A key is 'configured' only
    if <repo>/.claude/commands.json sets it — so we never silently run a default
    that's wrong for the repo's toolchain (e.g. pytest on a Go repo)."""
    cfg = REPO_ROOT / ".claude" / "commands.json"
    cmds = dict(_DEFAULTS)
    configured: set = set()
    if cfg.exists():
        try:
            user = json.loads(cfg.read_text())
            for k in _DEFAULTS:
                if isinstance(user.get(k), str) and user[k].strip():
                    cmds[k] = user[k]
                    configured.add(k)
        except Exception:
            # malformed commands.json — fall back to built-in defaults rather
            # than failing; log so a broken override file is diagnosable.
            _log.warning("could not parse %s; using default commands", cfg,
                         exc_info=True)
    return cmds, configured


def _scrubbed_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}


def _repo_tier() -> int | None:
    """Best-effort read of this repo's privacy tier for the approval record."""
    import re
    pol = REPO_ROOT / ".claude" / "repo-policy.yaml"
    try:
        for line in pol.read_text().splitlines():
            m = re.match(r"\s*tier\s*:\s*([0-3])\b", line)
            if m:
                return int(m.group(1))
    except Exception:
        # tier is advisory metadata on the approval record; absence is fine.
        _log.debug("could not read repo tier from %s", pol, exc_info=True)
    return None


def _open_approvals_ui() -> None:
    """Surface an ACTUAL UI: reuse the running approvals server (discovered from
    the service registry) or auto-start one on a free port, then open the browser
    to whatever port it actually got. Beats a contentless OS notification. Disable
    with CLAUDE_ENV_APPROVAL_AUTO_UI=false."""
    if os.environ.get("CLAUDE_ENV_APPROVAL_AUTO_UI", "true").lower() != "true":
        return
    import time
    try:
        from lib.services import get as _svc_get
        svc = _svc_get("approvals")
        if svc is None:                               # not running -> start it
            ui = _HOME / "agents" / "orchestration" / "approvals_ui.py"
            cmd = [sys.executable, str(ui)]
            pref = os.environ.get("CLAUDE_ENV_APPROVAL_PORT")
            if pref:                                  # optional preferred-port hint
                cmd += ["--port", pref]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            for _ in range(30):                       # wait for it to register (~3s)
                svc = _svc_get("approvals")
                if svc:
                    break
                time.sleep(0.1)
        if not svc:
            return
        url = svc["url"]
        if sys.platform == "darwin":
            subprocess.run(["open", url], capture_output=True, timeout=5)
        elif sys.platform.startswith("linux"):
            subprocess.run(["xdg-open", url], capture_output=True, timeout=5)
    except Exception:
        # auto-opening the approval UI is a convenience; the request still blocks
        # and the operator can open the URL manually. Log the failure.
        _log.info("could not auto-open approval UI in a browser", exc_info=True)


async def _await_decision(req_id: str) -> tuple[str, str | None]:
    """Block until the operator approves/denies this request (or we time out).
    Polls the human_approvals row the approvals UI updates in another process."""
    import asyncio
    from lib.db import get_db
    db = get_db()
    waited = 0
    while waited < _WAIT_S:
        row = db.query_one(
            "SELECT decision, decided_by FROM human_approvals WHERE request_id=?",
            (req_id,))
        if row and row["decision"] in ("approved", "denied"):
            return row["decision"], row["decided_by"]
        await asyncio.sleep(_POLL_S)
        waited += _POLL_S
    return "pending", None


def _run(template: str, kind: str) -> str:
    argv = shlex.split(template)
    if not argv:
        return "ERROR: empty command"
    try:
        proc = subprocess.run(
            argv, cwd=str(REPO_ROOT), env=_scrubbed_env(),
            capture_output=True, text=True, timeout=TIMEOUT_S, shell=False)
    except subprocess.TimeoutExpired:
        _audit.tool_call(tool=f"terminal.{kind}", args={"cmd": template},
                         result_kind="timeout")
        return f"TIMEOUT after {TIMEOUT_S}s: {template}"
    except FileNotFoundError:
        return f"ERROR: command not found: {argv[0]}"
    _audit.tool_call(tool=f"terminal.{kind}", args={"cmd": template},
                     result_kind=f"exit{proc.returncode}")
    tail = (proc.stdout or "")[-6000:] + (("\n[stderr]\n" + proc.stderr[-2000:])
                                          if proc.stderr else "")
    return f"exit={proc.returncode}\n{tail}"


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="terminal.run_tests",
             description="Run this repo's configured test command (from "
                         "<repo>/.claude/commands.json) in a sandbox: repo-root cwd, "
                         "scrubbed env (no inherited secrets), shell=False argv-only (no "
                         "pipes/interpolation), hard timeout. Takes no arguments — you cannot "
                         "choose the command, only trigger the vetted one. If no test command "
                         "is configured it runs NOTHING and returns a 'NOT CONFIGURED' "
                         "directive. Returns exit code + truncated stdout/stderr; audited.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run_benchmarks",
             description="Run this repo's configured benchmark command from "
                         "<repo>/.claude/commands.json, in the same sandbox as run_tests "
                         "(repo-root cwd, scrubbed env, argv-only, timeout). Takes no "
                         "arguments. Returns 'NOT CONFIGURED' and runs nothing if unset; "
                         "otherwise returns exit code + truncated output. Audited.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run_audit",
             description="Run this repo's configured security-audit command (e.g. pip-audit) "
                         "from <repo>/.claude/commands.json, in the same sandbox as run_tests "
                         "(repo-root cwd, scrubbed env, argv-only, timeout). Takes no "
                         "arguments. Returns 'NOT CONFIGURED' and runs nothing if unset; "
                         "otherwise returns exit code + truncated output. Audited.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run",
             description="Request execution of an arbitrary (typically state-mutating) command "
                         "that isn't one of the vetted run_tests/benchmarks/audit templates. "
                         "This opens a human-approval request, surfaces the approvals web UI, "
                         "and BLOCKS until an operator approves or denies (or it times out, "
                         "~120s). On approval it runs in the SAME sandbox — repo-root cwd, "
                         "scrubbed env, shell=False argv-only (no pipes/redirection/shell "
                         "features), timeout — and returns who approved plus exit code and "
                         "output; on denial/timeout nothing runs. Use only when a human is "
                         "available to approve; the whole request is audited.",
             inputSchema={"type": "object",
                          "properties": {"command": {"type": "string",
                                          "description": "The command line to request, e.g. "
                                          "'npm install' or 'ruff format .'. Parsed with "
                                          "shlex into an argv list and run WITHOUT a shell, so "
                                          "pipes, redirects, '&&', globs and env-var expansion "
                                          "are NOT interpreted."}},
                          "required": ["command"]}),
    ]


def _run_configured(cmds: dict, configured: set, kind: str) -> str:
    """Run a command only if the repo explicitly configured it; otherwise return
    a clear directive (never blindly run the toolchain-specific default)."""
    if kind not in configured:
        return (f"NOT CONFIGURED: no '{kind}' command is set for this repo. Add it to "
                f"{REPO_ROOT}/.claude/commands.json — e.g. "
                f'{{"run_tests": "go test ./..."}} (Go), "npm test" (JS), '
                f'"pytest -q" (Python), "cargo test" (Rust), or "make test". '
                f"(No command was run.)")
    return _run(cmds[kind], kind)


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    cmds, configured = _load_commands()
    if name == "terminal.run_tests":
        return [TextContent(type="text", text=_run_configured(cmds, configured, "run_tests"))]
    if name == "terminal.run_benchmarks":
        return [TextContent(type="text",
                            text=_run_configured(cmds, configured, "run_benchmarks"))]
    if name == "terminal.run_audit":
        return [TextContent(type="text", text=_run_configured(cmds, configured, "run_audit"))]
    if name == "terminal.run":
        command = str(arguments.get("command", "")).strip()
        if not command:
            return [TextContent(type="text", text="ERROR: empty command")]
        _audit.security_event(category="terminal_gate", severity="low",
                              detail=f"state-mutating command requested: {command}",
                              source="terminal.run")
        # Open a human approval, surface the real UI, then BLOCK until the operator
        # decides. On approval the command runs (same sandbox: argv-only, no shell,
        # scrubbed env, repo-root cwd, timeout); on denial/timeout it does not.
        req_id = _audit.human_approval_request(
            agent="terminal", action=f"terminal.run: {command}", tier=_repo_tier())
        _open_approvals_ui()
        decision, by = await _await_decision(req_id)
        if decision == "approved":
            out = _run(command, "run")
            return [TextContent(type="text",
                    text=f"APPROVED by {by} (request {req_id}). Executed:\n{out}")]
        if decision == "denied":
            return [TextContent(type="text",
                    text=f"DENIED by {by} (request {req_id}). Command was NOT run.")]
        return [TextContent(type="text",
                text=f"NO DECISION within {_WAIT_S}s — request {req_id} is still pending. "
                     f"Approve it in the approvals UI, then ask me to run it again.")]
    return [TextContent(type="text", text=f"ERROR: unknown or denied tool {name}")]


async def _serve() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_serve())
