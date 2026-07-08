#!/usr/bin/env python3
"""
git MCP server :: read-mostly git access.

Exposes log, diff, blame, status, and show. State-mutating git operations
(push, amend, rebase, reset --hard, force) are denied here and must be performed
by a human or routed through the approval gate. `git.commit` is exposed but flagged
as approval-required (the orchestrator's ApprovalGate enforces this).

All git invocations use argv lists (shell=False) inside the repo root. stdio
server. Requires: pip install mcp
"""
from __future__ import annotations

import os
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
    sys.stderr.write("git: the 'mcp' package is required (pip install mcp)\n")
    raise

REPO_ROOT = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-git")
_audit = AuditLogger(session_id=SESSION_ID, actor="git-mcp", repo=REPO_ROOT.name)
server = Server("git")

# Explicitly denied subcommand fragments (defense in depth).
_DENY = ("push", "reset", "rebase", "--amend", "--force", "-f", "filter-branch",
         "remote add", "config")


def _git(args: list[str]) -> str:
    # final guard: reject anything that smells state-mutating
    joined = " ".join(args).lower()
    if any(tok in joined for tok in _DENY):
        _audit.security_event(category="git_denied", severity="medium",
                              detail=f"denied git args: {args}", source="git-mcp")
        return "DENIED: state-mutating git operation; route through approval gate."
    try:
        proc = subprocess.run(["git", *args], cwd=str(REPO_ROOT),
                              capture_output=True, text=True, timeout=60, shell=False)
    except FileNotFoundError:
        return "ERROR: git not found on PATH"
    except subprocess.TimeoutExpired:
        return "TIMEOUT"
    _audit.tool_call(tool=f"git.{args[0]}", args={"argv": args},
                     result_kind=f"exit{proc.returncode}")
    return (proc.stdout or proc.stderr or "")[-8000:]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="git.log",
             description="Read-only commit history via `git log --oneline --decorate` in the "
                         "repo root, most-recent first. Use to review recent commits or the "
                         "history touching one path. Does not mutate anything; state-changing "
                         "git ops (push/reset/rebase/amend/force/config) are denied by this "
                         "server. Runs with shell=False (argv only). Output is truncated to "
                         "the last ~8k chars and the call is audited.",
             inputSchema={"type": "object",
                          "properties": {"max_count": {"type": "integer",
                                          "description": "Max commits to return (maps to "
                                          "`-n`). Defaults to 30."},
                                         "path": {"type": "string",
                                          "description": "Optional repo-relative path to "
                                          "limit history to, e.g. 'src/app.py'."}}}),
        Tool(name="git.diff",
             description="Read-only `git diff`. With no args, diffs the working tree against "
                         "the index; pass ref_a (and optionally ref_b) to diff between commits/"
                         "branches. Use to inspect uncommitted changes or compare two refs. "
                         "Does not mutate; runs argv-only (shell=False). Output truncated to "
                         "the last ~8k chars; the call is audited.",
             inputSchema={"type": "object",
                          "properties": {"ref_a": {"type": "string",
                                          "description": "First ref to compare, e.g. 'HEAD', "
                                          "'main', or a commit sha. Omit to diff the working "
                                          "tree."},
                                         "ref_b": {"type": "string",
                                          "description": "Second ref to compare against ref_a, "
                                          "e.g. 'feature-branch'. Optional."},
                                         "path": {"type": "string",
                                          "description": "Optional repo-relative path to limit "
                                          "the diff to, e.g. 'src/app.py'."}}}),
        Tool(name="git.blame",
             description="Read-only `git blame` for one file: shows the commit and author "
                         "responsible for each line. Use to find when/why a line changed. "
                         "argv-only (shell=False); output truncated to ~8k chars; audited.",
             inputSchema={"type": "object",
                          "properties": {"path": {"type": "string",
                                          "description": "Repo-relative file to blame, e.g. "
                                          "'src/app.py' (required)."}},
                          "required": ["path"]}),
        Tool(name="git.status",
             description="Read-only `git status --porcelain`: a compact, machine-readable list "
                         "of changed/staged/untracked files (empty output means a clean tree). "
                         "Takes no arguments. argv-only (shell=False); audited.",
             inputSchema={"type": "object", "properties": {}}),
        Tool(name="git.show",
             description="Read-only `git show <ref>`: display a commit (message + diff) or the "
                         "contents of an object at a ref. Use to inspect a specific commit or "
                         "a file at a given revision. argv-only (shell=False); output "
                         "truncated to ~8k chars; audited.",
             inputSchema={"type": "object",
                          "properties": {"ref": {"type": "string",
                                          "description": "Object/ref to show, e.g. a commit "
                                          "sha, 'HEAD~1', a tag, or 'HEAD:path/to/file' for a "
                                          "file at a revision (required)."}},
                          "required": ["ref"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "git.log":
        args = ["log", "--oneline", "--decorate",
                f"-n{int(arguments.get('max_count', 30))}"]
        if arguments.get("path"):
            args += ["--", arguments["path"]]
        return [TextContent(type="text", text=_git(args))]
    if name == "git.diff":
        args = ["diff"]
        if arguments.get("ref_a"):
            args.append(arguments["ref_a"])
        if arguments.get("ref_b"):
            args.append(arguments["ref_b"])
        if arguments.get("path"):
            args += ["--", arguments["path"]]
        return [TextContent(type="text", text=_git(args))]
    if name == "git.blame":
        return [TextContent(type="text", text=_git(["blame", "--", arguments["path"]]))]
    if name == "git.status":
        return [TextContent(type="text", text=_git(["status", "--porcelain"]))]
    if name == "git.show":
        return [TextContent(type="text", text=_git(["show", arguments["ref"]]))]
    return [TextContent(type="text", text=f"DENIED or unknown tool: {name}")]


async def _serve() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_serve())
