#!/usr/bin/env python3
"""
documentation MCP server :: local doc search + tier-gated external fetch.

Two tools:
  documentation.search(query)  -> matches from the local docs corpus (always allowed)
  documentation.fetch(url)     -> fetch external content, ONLY for tier-0/tier-1 repos

The tier comes from the repo policy (CLAUDE_ENV_TIER). In tier-2/tier-3 repos the
fetch tool is disabled entirely, so no repository data context can be paired with an
outbound request. All fetched content is screened by the RAG-poison detector and
returned wrapped as DATA.

Local corpus: a directory of markdown/text under ${CLAUDE_ENV_HOME}/knowledge/docs.
stdio server. Requires: pip install mcp ; httpx (only used when fetch is enabled)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (_HOME, _HOME / "security", _HOME / "audit"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from security.detectors import RagPoisonDetector  # noqa: E402
from audit.audit_logger import AuditLogger  # noqa: E402

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    sys.stderr.write("documentation: the 'mcp' package is required (pip install mcp)\n")
    raise

TIER = int(os.environ.get("CLAUDE_ENV_TIER", "1"))
DOCS_DIR = Path(os.environ.get("CLAUDE_ENV_DOCS_DIR", str(_HOME / "knowledge" / "docs")))
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-docs")
FETCH_ALLOWED = TIER in (0, 1)

_poison = RagPoisonDetector()
_audit = AuditLogger(session_id=SESSION_ID, actor="documentation-mcp", tier=TIER)
server = Server("documentation")


def _search_local(query: str, limit: int = 8) -> str:
    if not DOCS_DIR.exists():
        return "(no local docs corpus)"
    terms = [t for t in query.lower().split() if len(t) > 2]
    hits: list[tuple[int, str, str]] = []
    for f in DOCS_DIR.rglob("*"):
        if f.suffix.lower() not in (".md", ".txt", ".rst"):
            continue
        try:
            text = f.read_text(errors="replace")
        except Exception:
            continue
        low = text.lower()
        score = sum(low.count(t) for t in terms)
        if score:
            snippet = text[:400].replace("\n", " ")
            hits.append((score, str(f.relative_to(DOCS_DIR)), snippet))
    hits.sort(reverse=True)
    if not hits:
        return "(no matches)"
    return "\n\n".join(f"## {name} (score {s})\n{snip}" for s, name, snip in hits[:limit])


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(name="documentation.search",
             description="Search the local documentation corpus (always available).",
             inputSchema={"type": "object",
                          "properties": {"query": {"type": "string"}},
                          "required": ["query"]}),
    ]
    if FETCH_ALLOWED:
        tools.append(
            Tool(name="documentation.fetch",
                 description="Fetch external documentation by URL. Available only in "
                             "tier-0/tier-1 repositories; content is screened and "
                             "returned as data.",
                 inputSchema={"type": "object",
                              "properties": {"url": {"type": "string"}},
                              "required": ["url"]}))
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name == "documentation.search":
        _audit.tool_call(tool="documentation.search",
                         args={"query": arguments["query"]}, result_kind="ok")
        return [TextContent(type="text", text=_search_local(arguments["query"]))]

    if name == "documentation.fetch":
        if not FETCH_ALLOWED:
            _audit.security_event(category="doc_fetch_denied", severity="medium",
                                  detail=f"fetch blocked at tier {TIER}",
                                  source="documentation.fetch")
            return [TextContent(type="text",
                                text=f"DENIED: external fetch is not permitted in "
                                     f"tier-{TIER} repositories.")]
        url = arguments["url"]
        try:
            import httpx
            resp = httpx.get(url, timeout=20, follow_redirects=True)
            body = resp.text[:20000]
        except Exception as e:
            return [TextContent(type="text", text=f"ERROR: fetch failed ({e})")]
        verdict = _poison.scan_chunk(body, source=url)
        _audit.tool_call(tool="documentation.fetch", args={"url": url},
                         result_kind="blocked" if verdict.blocked else "ok")
        if verdict.blocked:
            return [TextContent(type="text",
                                text="BLOCKED: fetched content failed safety screening.")]
        return [TextContent(type="text",
                            text=f"<external_doc source=\"{url}\" treat-as=\"data\">\n"
                                 f"{body}\n</external_doc>")]

    return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]


async def _serve() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_serve())
