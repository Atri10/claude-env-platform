#!/usr/bin/env python3
"""
filesystem-policy MCP server :: the single sanctioned path to the filesystem.

Every file read, write, and listing requested by any agent flows through here.
Before a byte is returned or written, this server:

  1. resolves the path inside the repo root (rejecting traversal / absolute escapes)
  2. runs the repo PolicyEngine.evaluate_path -> allow | block
  3. on read: runs scan_content -> redacts or blocks secret material
  4. on write: re-evaluates the destination AND scans the payload for secrets
  5. records an audit event (and a policy_violation row on any block)

This is the enforcement chokepoint named in the architecture: nothing downstream
is trusted to re-check policy, so this server is intentionally strict and fail-closed.

Runs over stdio (no network). Requires the `mcp` package:  pip install mcp

Tools exposed:
  filesystem.read(path)            -> {content, redactions}
  filesystem.write(path, content)  -> {written, bytes}   (subject to approval upstream)
  filesystem.list(path)            -> {entries}
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# --- locate the platform code so we can import policy + audit -----------------
_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (_HOME, _HOME / "security", _HOME / "audit", _HOME / "lib"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from security.policy_engine import PolicyEngine  # noqa: E402
from audit.audit_logger import AuditLogger  # noqa: E402

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:  # pragma: no cover - surfaced clearly at runtime
    sys.stderr.write("filesystem-policy: the 'mcp' package is required "
                     "(pip install mcp)\n")
    raise

REPO_ROOT = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
GLOBAL_POLICY = os.environ.get(
    "CLAUDE_ENV_GLOBAL_POLICY", str(_HOME / "config" / "global-policy.yaml"))
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-fs")
MAX_READ_BYTES = int(os.environ.get("CLAUDE_ENV_MAX_READ_BYTES", str(2_000_000)))

_engine = PolicyEngine.load(REPO_ROOT, GLOBAL_POLICY)
_audit = AuditLogger(session_id=SESSION_ID, actor="filesystem-policy",
                     repo=REPO_ROOT.name, tier=_engine.repo.tier)

server = Server("filesystem-policy")


# --- path safety -------------------------------------------------------------
class PolicyBlocked(Exception):
    pass


def _resolve(rel_path: str) -> tuple[str, Path]:
    """Resolve a repo-relative path, rejecting escapes. Returns (rel, abs)."""
    candidate = (REPO_ROOT / rel_path).resolve()
    try:
        rel = candidate.relative_to(REPO_ROOT)
    except ValueError:
        raise PolicyBlocked(f"path escapes repo root: {rel_path}")
    return str(rel).replace("\\", "/"), candidate


def _enforce_path(rel: str) -> None:
    d = _engine.evaluate_path(rel)
    if d.action == "block":
        _audit.policy_violation(path=rel, rule=d.rule or d.reason,
                                decision="block", tier=_engine.repo.tier)
        raise PolicyBlocked(f"policy blocked '{rel}': {d.reason}")


# --- tools -------------------------------------------------------------------
@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="filesystem.read",
             description="Read a repo-relative file. Policy + secret scan enforced; "
                         "secrets are redacted or the read is blocked.",
             inputSchema={"type": "object",
                          "properties": {"path": {"type": "string"}},
                          "required": ["path"]}),
        Tool(name="filesystem.write",
             description="Write a repo-relative file. Destination policy + payload "
                         "secret scan enforced. Approval gating is applied upstream.",
             inputSchema={"type": "object",
                          "properties": {"path": {"type": "string"},
                                         "content": {"type": "string"}},
                          "required": ["path", "content"]}),
        Tool(name="filesystem.list",
             description="List entries under a repo-relative directory, omitting "
                         "policy-blocked paths.",
             inputSchema={"type": "object",
                          "properties": {"path": {"type": "string"}},
                          "required": ["path"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "filesystem.read":
            return _do_read(arguments["path"])
        if name == "filesystem.write":
            return _do_write(arguments["path"], arguments["content"])
        if name == "filesystem.list":
            return _do_list(arguments["path"])
        raise PolicyBlocked(f"unknown tool: {name}")
    except PolicyBlocked as e:
        _audit.tool_call(tool=name, args={"path": arguments.get("path")},
                         result_kind="blocked")
        return [TextContent(type="text", text=f"BLOCKED: {e}")]
    except FileNotFoundError:
        return [TextContent(type="text", text="ERROR: file not found")]
    except Exception as e:  # fail closed, never leak a stack trace to the agent
        _audit.security_event(category="mcp_fs_error", severity="medium",
                              detail=str(e), source=name)
        return [TextContent(type="text", text="ERROR: request could not be served")]


def _do_read(path: str) -> list[TextContent]:
    rel, abs_path = _resolve(path)
    _enforce_path(rel)
    if abs_path.stat().st_size > MAX_READ_BYTES:
        raise PolicyBlocked(f"file exceeds max read size ({MAX_READ_BYTES} bytes)")
    raw = abs_path.read_text(errors="replace")
    redacted, hits = _engine.scan_content(raw)
    if hits and hits[0][1] == -1:                      # full block signalled
        _audit.policy_violation(path=rel, rule="content_scan:block",
                                decision="block", tier=_engine.repo.tier)
        raise PolicyBlocked(f"secret content in '{rel}'; read blocked")
    if hits:
        _audit.security_event(category="secret_redaction", severity="low",
                              detail=f"{rel}: {hits}", source="filesystem.read")
    _audit.tool_call(tool="filesystem.read", args={"path": rel},
                     result_kind="ok")
    note = f"  [redactions: {hits}]" if hits else ""
    return [TextContent(type="text", text=redacted + ("" if not note else f"\n{note}"))]


def _do_write(path: str, content: str) -> list[TextContent]:
    rel, abs_path = _resolve(path)
    _enforce_path(rel)
    # scan the OUTGOING payload too: never let a secret be written to disk
    scrubbed, hits = _engine.scan_content(content)
    if hits and hits[0][1] == -1:
        _audit.policy_violation(path=rel, rule="content_scan:block_write",
                                decision="block", tier=_engine.repo.tier)
        raise PolicyBlocked(f"refusing to write secret content to '{rel}'")
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(scrubbed)
    _audit.tool_call(tool="filesystem.write",
                     args={"path": rel, "bytes": len(scrubbed)}, result_kind="ok")
    _audit.agent_action(agent="filesystem-policy", action="write", target=rel,
                        summary=f"{len(scrubbed)} bytes", success=True)
    return [TextContent(type="text",
                        text=f"WROTE {rel} ({len(scrubbed)} bytes)"
                             + (f"; redacted {hits}" if hits else ""))]


def _do_list(path: str) -> list[TextContent]:
    rel, abs_path = _resolve(path or ".")
    if not abs_path.is_dir():
        raise PolicyBlocked(f"not a directory: {rel}")
    entries = []
    for child in sorted(abs_path.iterdir()):
        crel = str(child.relative_to(REPO_ROOT)).replace("\\", "/")
        if _engine.evaluate_path(crel).action == "block":
            continue                                   # hide blocked paths entirely
        entries.append(crel + ("/" if child.is_dir() else ""))
    _audit.tool_call(tool="filesystem.list", args={"path": rel},
                     result_kind="ok")
    return [TextContent(type="text", text="\n".join(entries))]


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_run())
