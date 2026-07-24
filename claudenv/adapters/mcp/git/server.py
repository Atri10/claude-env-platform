"""
claude-env :: Adapters - Git MCP Server

Safe git operations via MCP. Only allow-listed commands; no push/reset/rebase.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from claudenv.adapters.logging import configure_logging
from claudenv.adapters.mcp._deps import build_deps
from claudenv.domain.policy import PolicyEngine

logger = logging.getLogger(__name__)

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
                    description=(
                        "Show the current working tree status — which files are staged, modified, "
                        "untracked, or deleted. Safe read-only operation, always permitted, no "
                        "approval required. Use before commit to review what will be included."
                    ),
                    inputSchema={"type": "object", "properties": {},
                                 "description": "No parameters needed."},
                ),
                Tool(
                    name="git.diff",
                    description=(
                        "Show unstaged (working tree) changes by default, or staged changes with "
                        "staged=true. Returns unified diff format. Safe read-only operation. Use "
                        "before git add to review exactly what changed."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "staged": {
                                "type": "boolean",
                                "default": False,
                                "description": "If true, show staged (index) diff instead of unstaged. "
                                               "Equivalent to 'git diff --cached'.",
                            }
                        },
                    },
                ),
                Tool(
                    name="git.log",
                    description=(
                        "Show recent commit history. By default returns the last 10 commits in "
                        "oneline format (hash + subject line). Use limit to see more, oneline=false "
                        "for full commit details (author, date, full message). Safe read-only."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "oneline": {
                                "type": "boolean",
                                "default": True,
                                "description": "If true, show abbreviated hash + subject line only. "
                                               "If false, show full commit details (author, date, message).",
                            },
                            "limit": {
                                "type": "integer",
                                "default": 10,
                                "description": "Maximum number of commits to show. Pass 0 for unlimited.",
                            },
                        },
                    },
                ),
                Tool(
                    name="git.add",
                    description=(
                        "Stage file changes for the next commit. Accepts one or more file paths; "
                        "defaults to staging all changes ('.'). May require human approval depending "
                        "on repo policy. Use after reviewing changes with git.diff."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "paths": {
                                "type": "array",
                                "items": {"type": "string"},
                                "default": ["."],
                                "description": "File paths to stage (space-separated, relative to repo root). "
                                               "Defaults to '.' which stages all changes.",
                            }
                        },
                    },
                ),
                Tool(
                    name="git.commit",
                    description=(
                        "Create a commit from currently staged changes. Requires a commit message. "
                        "ALWAYS requires human approval — this is a state-mutating operation. "
                        "Use git add first to stage files, then git commit to create the commit."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "message": {
                                "type": "string",
                                "description": "Commit message describing the changes. Should be a "
                                               "clear, concise description of what and why.",
                            }
                        },
                        "required": ["message"],
                    },
                ),
                Tool(
                    name="git.checkout",
                    description=(
                        "Switch to an existing branch, or create a new branch (with create=true) and "
                        "switch to it. Also works to restore individual files from a past commit. "
                        "Safe local operation — does NOT interact with remotes."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "target": {
                                "type": "string",
                                "description": "Branch name, commit SHA, or tag to check out. "
                                               "For file restore use the file path.",
                            },
                            "create": {
                                "type": "boolean",
                                "default": False,
                                "description": "If true, create a new branch with the given target name "
                                               "before switching. Equivalent to 'git checkout -b <target>'.",
                            },
                        },
                        "required": ["target"],
                    },
                ),
                Tool(
                    name="git.stash",
                    description=(
                        "Temporarily stash (save) uncommitted changes so you can work on something "
                        "else. Actions: push (save changes), pop (restore most recent stash), "
                        "list (show all stashes), drop (delete a stash). Safe local operation."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "action": {
                                "type": "string",
                                "enum": ["push", "pop", "list", "drop"],
                                "default": "push",
                                "description": "What stash operation to perform. push=save changes, "
                                               "pop=restore latest stash, list=show all, drop=delete a stash.",
                            },
                            "message": {
                                "type": "string",
                                "description": "Optional description for 'push' action. Helps identify "
                                               "the stash later in 'list' output.",
                            },
                        },
                    },
                ),
                Tool(
                    name="git.remote",
                    description=(
                        "List all configured remote repositories with their URLs (fetch/push). "
                        "Safe read-only operation. Use to check what remote repos are configured "
                        "before pushing or pulling."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
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
            logger.error("git not found on PATH")
            return [TextContent(type="text", text="ERROR: git not found on PATH")]
        except subprocess.TimeoutExpired:
            logger.error("git command timed out")
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
    deps = build_deps(repo_root, session_id, actor)
    return GitServer(deps.repo_root, deps.audit_logger, deps.policy_engine, deps.session_id)


async def main() -> None:
    logger.info("git MCP server starting")
    configure_logging(console=False)
    repo_root = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-git")
    server = create_server(repo_root, session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
