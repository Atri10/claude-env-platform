#!/usr/bin/env python3
"""
memory-graph MCP server :: namespaced access to the local memory graph (SQLite).

Exposes recall/read/write/expand over the memory graph. Namespace isolation is
the central control: each repo runs this server with CLAUDE_ENV_MEMORY_NS set to
its own namespace and CLAUDE_ENV_MEMORY_ISOLATED governing whether cross-namespace
reads are even attempted. Isolated namespaces never see another repo's memory.

Writes and reads are audited by the underlying MemoryManager / MemoryRetriever.
Destructive operations (delete/prune) are NOT exposed here; they live in the
memory_pruner CLI behind the approval gate.

stdio server. Requires: pip install mcp
Tools:
  memory.recall(query, depth?, top_k?)       -> ranked nodes (keyword+graph expand)
  memory.read(node_id)                        -> single node
  memory.expand(seed_ids[], depth?, rels?)    -> connected subgraph
  memory.write(memory_type, node_kind, name, body, repo?, confidence?) -> node_id
  memory.link(src, dst, rel, weight?)         -> edge_id
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (_HOME, _HOME / "memory"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from memory.memory_manager import HALF_LIFE, VALID_KINDS, MemoryManager  # noqa: E402
from memory.memory_retriever import MemoryRetriever  # noqa: E402

try:
    sys.path.insert(0, str(_HOME))
    # Use the shared RAG factory so memory nodes embed with the SAME model the
    # rest of the stack uses (driven by config/rag.yaml + env, no hardcoding).
    from rag.config import get_embedder
    _embedder = get_embedder()
except Exception as _emb_err:
    sys.stderr.write(f"memory-graph: embedding model unavailable ({_emb_err}); "
                     "nodes will be stored without embeddings\n")
    _embedder = None

try:
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
    from mcp.types import TextContent, Tool
except ImportError:
    sys.stderr.write("memory-graph: the 'mcp' package is required (pip install mcp)\n")
    raise

NS = os.environ.get("CLAUDE_ENV_MEMORY_NS", "global")
ISOLATED = os.environ.get("CLAUDE_ENV_MEMORY_ISOLATED", "false").lower() == "true"
SESSION_ID = os.environ.get("CLAUDE_ENV_SESSION", "mcp-memory")
WRITE_ENABLED = os.environ.get("CLAUDE_ENV_MEMORY_WRITE", "true").lower() == "true"

server = Server("memory-graph")
_mgr = MemoryManager(namespace=NS, session_id=SESSION_ID, actor="memory-mcp",
                     isolated=ISOLATED)
_retr = MemoryRetriever(namespace=NS, session_id=SESSION_ID, actor="memory-mcp",
                        isolated=ISOLATED)


@server.list_tools()
async def list_tools() -> list[Tool]:
    tools = [
        Tool(name="memory.recall",
             description="Recall memory by query: keyword seeds expanded through the "
                         "graph, ranked by similarity and time-decayed confidence.",
             inputSchema={"type": "object",
                          "properties": {"query": {"type": "string"},
                                         "depth": {"type": "integer"},
                                         "top_k": {"type": "integer"}},
                          "required": ["query"]}),
        Tool(name="memory.read",
             description="Read a single memory node by id (touches access stats).",
             inputSchema={"type": "object",
                          "properties": {"node_id": {"type": "string"}},
                          "required": ["node_id"]}),
        Tool(name="memory.expand",
             description="Walk the memory graph from seed node ids up to `depth` hops.",
             inputSchema={"type": "object",
                          "properties": {"seed_ids": {"type": "array",
                                                      "items": {"type": "string"}},
                                         "depth": {"type": "integer"},
                                         "rels": {"type": "array",
                                                  "items": {"type": "string"}}},
                          "required": ["seed_ids"]}),
    ]
    if WRITE_ENABLED:
        tools += [
            Tool(name="memory.write",
                 description="Create a memory node in this server's namespace. "
                             "node_kind must match memory_type: episodic={session,"
                             "decision,investigation}, semantic={entity,concept,"
                             "architecture,preference}, procedural={workflow,"
                             "convention,pattern}, agent=any of the above. Use "
                             "'decision' for a confirmed-but-deferred issue/finding "
                             "(it gets a 365-day half-life and is never auto-pruned) "
                             "and 'investigation' for an open/unresolved one.",
                 inputSchema={"type": "object",
                              "properties": {
                                  "memory_type": {"type": "string",
                                                  "enum": list(VALID_KINDS)},
                                  "node_kind": {"type": "string",
                                                "enum": sorted(HALF_LIFE)},
                                  "name": {"type": "string"},
                                  "body": {"type": "object"},
                                  "repo": {"type": "string"},
                                  "confidence": {"type": "number"}},
                              "required": ["memory_type", "node_kind", "name", "body"]}),
            Tool(name="memory.link",
                 description="Create a typed edge between two nodes in this namespace.",
                 inputSchema={"type": "object",
                              "properties": {"src": {"type": "string"},
                                             "dst": {"type": "string"},
                                             "rel": {"type": "string"},
                                             "weight": {"type": "number"}},
                              "required": ["src", "dst", "rel"]}),
        ]
    return tools


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "memory.recall":
            query_vec = None
            if _embedder is not None:
                try:
                    query_vec = _embedder.embed_query(arguments["query"])
                except Exception:
                    pass
            rows = _retr.recall(arguments["query"],
                                depth=int(arguments.get("depth", 2)),
                                top_k=int(arguments.get("top_k", 10)),
                                query_vec=query_vec)
            return [TextContent(type="text", text=json.dumps(rows, default=str, indent=2))]
        if name == "memory.read":
            node = _mgr.get_node(arguments["node_id"])
            if node is None:
                return [TextContent(type="text", text="null")]
            return [TextContent(type="text", text=json.dumps(node, default=str, indent=2))]
        if name == "memory.expand":
            rows = _retr.expand(arguments["seed_ids"],
                                depth=int(arguments.get("depth", 2)),
                                rels=arguments.get("rels"))
            return [TextContent(type="text", text=json.dumps(rows, default=str, indent=2))]
        if name == "memory.write":
            if not WRITE_ENABLED:
                return [TextContent(type="text", text="ERROR: memory writes disabled")]
            # MemoryManager.add_node embeds the node itself (best-effort, cached
            # embedder), so recall can find it. We deliberately don't compute the
            # blob here — the previous inline path referenced an unimported symbol
            # and silently stored NULL embeddings.
            node_id = _mgr.add_node(
                memory_type=arguments["memory_type"], node_kind=arguments["node_kind"],
                name=arguments["name"], body=arguments["body"],
                repo=arguments.get("repo"),
                confidence=float(arguments.get("confidence", 1.0)))
            return [TextContent(type="text", text=node_id)]
        if name == "memory.link":
            if not WRITE_ENABLED:
                return [TextContent(type="text", text="ERROR: memory writes disabled")]
            edge_id = _mgr.add_edge(arguments["src"], arguments["dst"],
                                    arguments["rel"],
                                    weight=float(arguments.get("weight", 1.0)))
            return [TextContent(type="text", text=edge_id)]
        return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]
    except Exception as e:
        return [TextContent(type="text", text=f"ERROR: {e}")]


async def _run() -> None:
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(_run())
