#!/usr/bin/env python3
"""
lancedb-rag MCP server :: read-only hybrid retrieval over the local LanceDB index.

Wraps the rag.pipelines.retrieve.Retriever. Results are always wrapped in the
<retrieved_context> data delimiters produced by the pipeline and screened by the
RAG-poison detector before being returned, so retrieved text can never act as an
instruction to the agent.

Indexing is out of band (rag/bootstrap_rag.py, incremental_index.py); this server
never writes to the index.

stdio server. Requires: pip install mcp lancedb
Tool:
  lancedb.search(query, repo, branch?, top_n?) -> ranked, delimited context
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (_HOME, _HOME / "rag", _HOME / "security"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from rag.pipelines.retrieve import Retriever  # noqa: E402
from security.detectors import RagPoisonDetector  # noqa: E402

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    sys.stderr.write("lancedb-rag: the 'mcp' package is required (pip install mcp)\n")
    raise

SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-rag")
DEFAULT_REPO = os.environ.get("CLAUDE_ENV_REPO_NAME", Path(os.getcwd()).name)
DEFAULT_BRANCH = os.environ.get("CLAUDE_ENV_BRANCH", "main")

server = Server("lancedb-rag")
_poison = RagPoisonDetector()
_retrievers: dict[tuple[str, str], Retriever] = {}


def _get_retriever(repo: str, branch: str) -> Retriever:
    key = (repo, branch)
    if key not in _retrievers:
        # Retriever loads the embedder/reranker once and caches per repo+branch.
        _retrievers[key] = Retriever(repo=repo, branch=branch,
                                     session_id=SESSION_ID, actor="rag-mcp")
    return _retrievers[key]


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(name="lancedb.search",
             description="Hybrid (vector + keyword) search over the local code/doc "
                         "index, reranked. Returns delimited, data-wrapped context.",
             inputSchema={"type": "object",
                          "properties": {
                              "query": {"type": "string"},
                              "repo": {"type": "string"},
                              "branch": {"type": "string"},
                              "top_n": {"type": "integer", "minimum": 1, "maximum": 20}},
                          "required": ["query"]}),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    if name != "lancedb.search":
        return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]
    query = arguments["query"]
    repo = arguments.get("repo", DEFAULT_REPO)
    branch = arguments.get("branch", DEFAULT_BRANCH)
    top_n = int(arguments.get("top_n", 8))
    try:
        retr = _get_retriever(repo, branch)
        hits = retr.query(text=query, top_n=top_n)
    except Exception as e:
        return [TextContent(type="text",
                            text=f"ERROR: retrieval unavailable ({e})")]

    # screen each result for poisoning before handing it back
    safe: list[str] = []
    for hit in hits:
        text = hit.get("wrapped") or hit.get("text", "")
        verdict = _poison.scan_chunk(hit.get("text", ""),
                                     source=f"{repo}@{branch}:{hit.get('file_path','?')}")
        if verdict.blocked:
            continue
        safe.append(text)
    if not safe:
        return [TextContent(type="text", text="<retrieved_context/> (no safe results)")]
    return [TextContent(type="text", text="\n\n".join(safe))]


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_run())
