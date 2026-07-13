"""
claude-env :: Adapters - Filesystem Policy MCP Server

Thin adapter exposing the PolicyEngine over MCP stdio.
All policy logic lives in domain/policy_engine.py; this is just transport.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import signal
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pathlib import Path

from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.config import get_config
from claudenv.adapters.persistence import SQLiteDatabase
from claudenv.domain.policy import PolicyEngine, PolicyDecision, PolicyService
from claudenv.domain.value_objects import SessionId
from claudenv.ports import IAuditLogger

# Constants
SCRATCH_PREFIX = "scratch://"
MAX_READ_BYTES = int(os.environ.get("CLAUDE_ENV_MAX_READ_BYTES", "2000000"))


class PolicyBlocked(Exception):
    """Raised when policy denies an operation."""
    pass


class FilesystemPolicyServer:
    """Filesystem policy enforcement MCP server."""

    def __init__(
            self,
            repo_root: Path,
            audit_logger: IAuditLogger,
            policy_engine: PolicyEngine,
            session_id: str = "mcp-fs",
    ):
        self.repo_root = repo_root
        self.audit = audit_logger
        self.engine = policy_engine
        self.session_id = session_id

        # Scratch directory
        config = get_config()
        home = Path(config.get_claude_env_home())
        self.scratch_root = (home / "scratch" / repo_root.name).resolve()

        # Create server
        self.server = Server("filesystem-policy")
        self._register_tools()
        self._register_handlers()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="filesystem.read",
                    description=(
                        "Read a file through the policy chokepoint. Path is resolved inside "
                        "repo root (traversal/absolute escapes rejected), evaluated by policy "
                        "engine, and content is secret-scanned. Matched secrets are redacted "
                        "in-line; hard-block secrets refuse the read. Fails closed on policy-"
                        "blocked paths and files over max read size (~2 MB). Every call audited. "
                        "Use 'scratch://' prefix for disposable work in per-repo scratch directory."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Repo-relative path (e.g. 'src/app.py'). Absolute paths and "
                                    "'..' segments that escape the repo root are rejected. "
                                    "Prefix with 'scratch://' (e.g. 'scratch://notes.txt') to "
                                    "read from the disposable per-repo scratch directory."
                                ),
                            }
                        },
                        "required": ["path"],
                    },
                ),
                Tool(
                    name="filesystem.write",
                    description=(
                        "Write text to a file through the policy chokepoint (creating parent "
                        "directories, overwriting if exists). Destination is policy-evaluated "
                        "and payload is secret-scanned before writing: detected secrets are "
                        "scrubbed, hard-block secrets refuse the write. Policy-blocked "
                        "destinations fail closed. Write is audited (tool_call + agent_action). "
                        "Use 'scratch://' prefix for disposable work."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Repo-relative destination path (e.g. 'src/new_module.py'). "
                                    "Escapes above repo root rejected. Prefix with 'scratch://' "
                                    "for disposable per-repo scratch directory."
                                ),
                            },
                            "content": {
                                "type": "string",
                                "description": "Full UTF-8 text to write; replaces existing file. "
                                               "Secrets scrubbed before hitting disk.",
                            },
                        },
                        "required": ["path", "content"],
                    },
                ),
                Tool(
                    name="filesystem.list",
                    description=(
                        "List immediate entries of a directory (one level, non-recursive), "
                        "with trailing '/' on subdirectories. Policy-blocked children are "
                        "hidden entirely. Returns 'BLOCKED: not a directory' if path is a file. "
                        "Call audited. Use 'scratch://' for disposable scratch directory."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {
                                "type": "string",
                                "description": (
                                    "Repo-relative directory path (e.g. '.' for repo root). "
                                    "Escapes above repo root rejected. Use 'scratch://' for "
                                    "disposable scratch directory."
                                ),
                            }
                        },
                        "required": ["path"],
                    },
                ),
            ]

    def _register_handlers(self) -> None:
        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "filesystem.read":
                    return await self._do_read(arguments["path"])
                if name == "filesystem.write":
                    return await self._do_write(arguments["path"], arguments["content"])
                if name == "filesystem.list":
                    return await self._do_list(arguments.get("path", "."))
                raise PolicyBlocked(f"unknown tool: {name}")
            except PolicyBlocked as e:
                self.audit.tool_call(
                    tool=name,
                    args={"path": arguments.get("path")},
                    result_kind="blocked",
                )
                return [TextContent(type="text", text=f"BLOCKED: {e}")]
            except FileNotFoundError:
                return [TextContent(type="text", text="ERROR: file not found")]
            except Exception as e:
                self.audit.security_event(
                    category="mcp_fs_error", severity="medium",
                    detail=str(e), source=name,
                )
                return [TextContent(type="text", text="ERROR: request could not be served")]

    # --- Path resolution ---------------------------------------------------------

    def _resolve(self, rel_path: str) -> tuple[str, Path]:
        """Resolve repo-relative OR scratch:// path. Returns (rel, abs)."""
        if rel_path.startswith(SCRATCH_PREFIX):
            return self._resolve_scratch(rel_path)
        candidate = (self.repo_root / rel_path).resolve()
        try:
            rel = candidate.relative_to(self.repo_root)
        except ValueError:
            raise PolicyBlocked(f"path escapes repo root: {rel_path}")
        return str(rel).replace("\\", "/"), candidate

    def _resolve_scratch(self, rel_path: str) -> tuple[str, Path]:
        subpath = rel_path[len(SCRATCH_PREFIX):]
        candidate = (self.scratch_root / subpath).resolve()
        try:
            rel = candidate.relative_to(self.scratch_root)
        except ValueError:
            raise PolicyBlocked(f"scratch path escapes scratch root: {rel_path}")
        return f"{SCRATCH_PREFIX}{rel}".replace("\\", "/"), candidate

    def _enforce_path(self, rel: str) -> None:
        """Enforce policy on a resolved repo-relative path."""
        if rel.startswith(SCRATCH_PREFIX):
            return  # scratch is allow-all by construction
        decision: PolicyDecision = self.engine.evaluate_path(rel)
        if decision.action == "block":
            self.audit.policy_violation(
                path=rel, rule=decision.rule or decision.reason,
                decision="block", tier=self.engine.get_compiled().tier,
            )
            raise PolicyBlocked(f"policy blocked '{rel}': {decision.reason}")

    def _reset_scratch_dir(self) -> None:
        """Wipe and recreate the scratch directory."""
        if self.scratch_root.exists():
            shutil.rmtree(self.scratch_root, ignore_errors=True)
        self.scratch_root.mkdir(parents=True, exist_ok=True)

    def _register_scratch_cleanup(self) -> None:
        def _cleanup(*_args) -> None:
            shutil.rmtree(self.scratch_root, ignore_errors=True)

        signal.signal(signal.SIGTERM, lambda s, f: (_cleanup(), os._exit(1)))
        signal.signal(signal.SIGINT, lambda s, f: (_cleanup(), os._exit(1)))

    # --- Tool implementations ----------------------------------------------------

    async def _do_read(self, path: str) -> list[TextContent]:
        rel, abs_path = self._resolve(path)
        self._enforce_path(rel)

        if abs_path.stat().st_size > MAX_READ_BYTES:
            raise PolicyBlocked(f"file exceeds max read size ({MAX_READ_BYTES} bytes)")

        raw = abs_path.read_text(errors="replace")
        redacted, hits = self.engine.scan_content(raw)

        if hits and hits[0][1] == -1:  # full block signal
            self.audit.policy_violation(
                path=rel, rule="content_scan:block",
                decision="block", tier=self.engine.get_compiled().tier,
            )
            raise PolicyBlocked(f"secret content in '{rel}'; read blocked")

        if hits:
            self.audit.security_event(
                category="secret_redaction", severity="low",
                detail=f"{rel}: {hits}", source="filesystem.read",
            )

        self.audit.tool_call(
            tool="filesystem.read", args={"path": rel}, result_kind="ok"
        )

        note = f"  [redactions: {hits}]" if hits else ""
        return [TextContent(type="text", text=redacted + ("" if not note else f"\n{note}"))]

    async def _do_write(self, path: str, content: str) -> list[TextContent]:
        rel, abs_path = self._resolve(path)
        self._enforce_path(rel)

        # Scan outgoing payload
        scrubbed, hits = self.engine.scan_content(content)
        if hits and hits[0][1] == -1:
            self.audit.policy_violation(
                path=rel, rule="content_scan:block_write",
                decision="block", tier=self.engine.get_compiled().tier,
            )
            raise PolicyBlocked(f"refusing to write secret content to '{rel}'")

        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(scrubbed)

        self.audit.tool_call(
            tool="filesystem.write",
            args={"path": rel, "bytes": len(scrubbed)},
            result_kind="ok",
        )
        self.audit.agent_action(
            agent="filesystem-policy", action="write", target=rel,
            summary=f"{len(scrubbed)} bytes", success=True,
        )
        return [TextContent(
            type="text",
            text=f"Wrote {rel} ({len(scrubbed)} bytes)" + (f"; redacted {hits}" if hits else "")
        )]

    async def _do_list(self, path: str) -> list[TextContent]:
        rel, abs_path = self._resolve(path or ".")
        if not abs_path.is_dir():
            raise PolicyBlocked(f"not a directory: {rel}")

        is_scratch = rel.startswith(SCRATCH_PREFIX)
        entries = []
        for child in sorted(abs_path.iterdir()):
            if is_scratch:
                crel = f"{SCRATCH_PREFIX}{child.relative_to(self.scratch_root)}".replace("\\", "/")
            else:
                crel = str(child.relative_to(self.repo_root)).replace("\\", "/")
                if self.engine.evaluate_path(crel).action == "block":
                    continue
            entries.append(crel + ("/" if child.is_dir() else ""))

        self.audit.tool_call(
            tool="filesystem.list", args={"path": rel}, result_kind="ok"
        )
        return [TextContent(type="text", text="\n".join(entries))]

    async def run(self) -> None:
        """Run the MCP server over stdio."""
        self._reset_scratch_dir()
        self._register_scratch_cleanup()
        async with stdio_server() as (read, write):
            await self.server.run(read, write, self.server.create_initialization_options())


def create_server(
        repo_root: Path | str,
        session_id: str = "mcp-fs",
        actor: str = "filesystem-policy",
) -> FilesystemPolicyServer:
    """Factory to create a configured filesystem policy server."""
    repo_root = Path(repo_root).resolve()
    config = get_config()

    # Build policy engine from global + repo policy
    policy_engine = PolicyService(config).load_engine(str(repo_root))

    # Create audit logger
    db = SQLiteDatabase(config.get_database_dsn())
    audit_logger = SqliteAuditLogger(
        db=db,
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=repo_root.name,
        tier=policy_engine.get_compiled().tier,
    )

    return FilesystemPolicyServer(repo_root, audit_logger, policy_engine, session_id)


async def main() -> None:
    """Entry point for stdio MCP server."""
    repo_root = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-fs")
    server = create_server(repo_root, session_id)
    await server.run()


if __name__ == "__main__":
    asyncio.run(main())
