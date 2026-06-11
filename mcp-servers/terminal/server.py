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

Anything state-mutating maps to `terminal.run` which is intentionally NOT executed
here; it returns a directive to route through the approval gate. There is no
`terminal.exec_unrestricted` tool at all.

Configuration: commands come from `${repo}/.claude/commands.json` if present, else
the conservative defaults below. stdio server. Requires: pip install mcp
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

# Conservative defaults; overridable per-repo via .claude/commands.json
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


def _load_commands() -> dict:
    cfg = REPO_ROOT / ".claude" / "commands.json"
    cmds = dict(_DEFAULTS)
    if cfg.exists():
        try:
            user = json.loads(cfg.read_text())
            for k in _DEFAULTS:
                if isinstance(user.get(k), str) and user[k].strip():
                    cmds[k] = user[k]
        except Exception:
            pass
    return cmds


def _scrubbed_env() -> dict:
    return {k: v for k, v in os.environ.items() if k in _ENV_ALLOW}


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
             description="Run the repository's configured test command (read-only, "
                         "sandboxed cwd, scrubbed env, timeout).",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run_benchmarks",
             description="Run the repository's configured benchmark command.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run_audit",
             description="Run the repository's configured security-audit command.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="terminal.run",
             description="Request execution of a state-mutating command. NOT executed "
                         "here; returns an approval-gate directive.",
             inputSchema={"type": "object",
                          "properties": {"command": {"type": "string"}},
                          "required": ["command"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    cmds = _load_commands()
    if name == "terminal.run_tests":
        return [TextContent(type="text", text=_run(cmds["run_tests"], "run_tests"))]
    if name == "terminal.run_benchmarks":
        return [TextContent(type="text",
                            text=_run(cmds["run_benchmarks"], "run_benchmarks"))]
    if name == "terminal.run_audit":
        return [TextContent(type="text", text=_run(cmds["run_audit"], "run_audit"))]
    if name == "terminal.run":
        _audit.security_event(category="terminal_gate", severity="low",
                              detail=f"state-mutating command requested: "
                                     f"{arguments.get('command')}",
                              source="terminal.run")
        return [TextContent(type="text",
                            text="GATED: state-mutating commands must be approved. "
                                 "Route this through approval_gate.py before execution.")]
    return [TextContent(type="text", text=f"ERROR: unknown or denied tool {name}")]


async def _serve() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_serve())
