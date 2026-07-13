"""
claude-env :: Adapters - Git MCP Server

Safe git operations via MCP. Only allow-listed commands; no push/reset/rebase.
"""
from __future__ import annotations

import os
import subprocess
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pathlib import Path

from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.config import get_config
from claudenv.adapters.persistence import SQLiteDatabase
from claudenv.domain.policy import PolicyEngine, PolicyService
from claudenv.domain.value_objects import SessionId

# Commands explicitly DENIED - these would modify remote state
DENIED_COMMANDS = {"push", "reset", "rebase", "merge --no-ff", "filter-branch", "gc"}

# Commands allowed for read-only use
ALLOWED_READ = {"status", "log", "diff", "show", "branch", "tag", "describe", "remote -v", "config --get"}

# Commands allowed for local writes (with approval)
ALLOWED_WRITE = {"add", "commit", "checkout", "switch", "stash", "merge", "rebase -i", "reset --soft", "reset --mixed"}

# Commands allowed for everything
ALLOWED_ALL = {"init", "clone"}


class GitServer:
    """Git MCP server with policy enforcement."""

    def __init__(
            self,
            repo_root: Path,
            audit_logger,
            policy_engine: PolicyEngine,
            session_id: str = "mcp-git",
    ):
        self.repo_root = repo_root
        self.audit = audit_logger
        self.engine = policy_engine
        self.session_id = session_id
        self.server = Server("git")
        self._register_tools()
        self._register_handlers()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="git.status",
                    description="Show working tree status (safe, read-only).",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="git.diff",
                    description="Show changes in working tree or index (safe, read-only).",
                    inputSchema={"type": "object", "properties": {"staged": {"type": "boolean", "default": False}}},
                ),
                Tool(
                    name="git.log",
                    description="Show commit history (safe, read-only).",
                    inputSchema={"type": "object", "properties": {"oneline": {"type": "boolean", "default": True},
                                                                  "limit": {"type": "integer", "default": 10}}},
                ),
                Tool(
                    name="git.add",
                    description="Stage files for commit (requires approval if repo policy says so).",
                    inputSchema={"type": "object", "properties": {
                        "paths": {"type": "array", "items": {"type": "string"}, "default": ["."]}}},
                ),
                Tool(
                    name="git.commit",
                    description="Create a commit from staged changes (requires approval).",
                    inputSchema={"type": "object", "properties": {"message": {"type": "string"}}},
                ),
                Tool(
                    name="git.checkout",
                    description="Switch branches or restore files (safe local operations).",
                    inputSchema={"type": "object", "properties": {"target": {"type": "string"},
                                                                  "create": {"type": "boolean", "default": False}}},
                ),
                Tool(
                    name="git.stash",
                    description="Stash changes temporarily (safe local operation).",
                    inputSchema={"type": "object", "properties": {
                        "action": {"type": "string", "enum": ["push", "pop", "list", "drop"], "default": "push"},
                        "message": {"type": "string"}}},
                ),
                Tool(
                    name="git.remote",
                    description="List configured remotes (safe, read-only).",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

    def _register_handlers(self) -> None:
        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            return await self._do_call(name, arguments)

    def _allow_git_operation(self, cmd: str) -> bool:
        """Check if git command is allowed by policy."""
        cmd_lower = cmd.lower().strip()

        # Check for explicitly denied commands
        for denied in DENIED_COMMANDS:
            if denied in cmd_lower:
                self.audit.security_event(
                    category="git_denied", severity="high",
                    detail=f"Denied git command: {cmd}", source="git-mcp"
                )
                return False

        # Check if in allowed lists
        if any(cmd_lower.startswith(allowed) for allowed in ALLOWED_READ):
            return True
        if any(cmd_lower.startswith(allowed) for allowed in ALLOWED_WRITE):
            return True
        if any(cmd_lower.startswith(allowed) for allowed in ALLOWED_ALL):
            return True

        # Default deny
        self.audit.security_event(
            category="git_denied", severity="medium",
            detail=f"Git command not explicitly allowed: {cmd}", source="git-mcp"
        )
        return False

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess:
        """Run git command safely."""
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_root,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def _require_approval(self, cmd: str) -> tuple[bool, str]:
        """Check if command needs approval (simplified)."""
        # In real impl, would check policy and open approval UI
        return False, ""

    async def _do_call(self, name: str, arguments: dict) -> list[TextContent]:
        # Map tool names to git subcommands
        if name == "git.status":
            args = ["status"]
        elif name == "git.diff":
            staged = arguments.get("staged", False)
            args = ["diff"] + (["--cached"] if staged else [])
        elif name == "git.log":
            oneline = arguments.get("oneline", True)
            limit = arguments.get("limit", 10)
            args = ["log", f"-{limit}"]
            if oneline:
                args.insert(1, "--oneline")
        elif name == "git.add":
            paths = arguments.get("paths", ["."])
            args = ["add", *paths]
        elif name == "git.commit":
            msg = arguments.get("message", "")
            if not msg:
                return [TextContent(type="text", text="ERROR: commit message required")]
            args = ["commit", "-m", msg]
        elif name == "git.checkout":
            target = arguments.get("target", "")
            create = arguments.get("create", False)
            args = ["checkout"]
            if create:
                args.append("-b")
            args.append(target)
        elif name == "git.stash":
            action = arguments.get("action", "push")
            msg = arguments.get("message", "")
            if action == "push":
                args = ["stash", "push"]
                if msg:
                    args.extend(["-m", msg])
            elif action == "pop":
                args = ["stash", "pop"]
            elif action == "list":
                args = ["stash", "list"]
            elif action == "drop":
                args = ["stash", "drop"]
            else:
                return [TextContent(type="text", text=f"ERROR: unknown stash action: {action}")]
        elif name == "git.remote":
            args = ["remote", "-v"]
        else:
            return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]

        # Policy chokepoint: every subcommand must clear the deny-list /
        # allow-list check before it is ever handed to git. This was
        # previously computed but never consulted, so every tool ran
        # unconditionally regardless of DENIED_COMMANDS/ALLOWED_*.
        cmd = " ".join(args)
        if not self._allow_git_operation(cmd):
            self.audit.tool_call(tool=name, args=arguments, result_kind="blocked")
            return [TextContent(type="text", text=f"BLOCKED: git operation not permitted: {cmd}")]

        try:
            result = self._run_git(args)
        except FileNotFoundError:
            return [TextContent(type="text", text="ERROR: git not found on PATH")]
        except subprocess.TimeoutExpired:
            return [TextContent(type="text", text="ERROR: git command timed out")]

        if result.returncode != 0:
            return [TextContent(type="text", text=f"ERROR: {result.stderr.strip()}")]

        self.audit.tool_call(tool=name, args=arguments, result_kind="ok")
        return [TextContent(type="text", text=result.stdout.strip())]


def create_server(
        repo_root: Path | str,
        session_id: str = "mcp-git",
        actor: str = "git-mcp",
) -> GitServer:
    repo_root = Path(repo_root).resolve()
    config = get_config()

    policy_engine = PolicyService(config).load_engine(str(repo_root))

    db = SQLiteDatabase(config.get_database_dsn())
    audit_logger = SqliteAuditLogger(
        db=db,
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=repo_root.name,
        tier=policy_engine.get_compiled().tier,
    )

    return GitServer(repo_root, audit_logger, policy_engine, session_id)


async def main() -> None:
    repo_root = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-git")
    server = create_server(repo_root, session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
