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

from memory.memory_manager import (  # noqa: E402
    HALF_LIFE, VALID_KINDS, VALID_RELS, MemoryManager)
from memory.memory_retriever import MemoryRetriever  # noqa: E402
from lib.logging_setup import get_logger  # noqa: E402

# stderr=True: MCP servers must never write to stdout (stdio protocol channel),
# but stderr is safe and mirrors into logs/mcp-memory-graph.log for triage.
_log = get_logger("mcp-memory-graph", stderr=True)

try:
    sys.path.insert(0, str(_HOME))
    # Use the shared RAG factory so memory nodes embed with the SAME model the
    # rest of the stack uses (driven by config/rag.yaml + env, no hardcoding).
    from rag.config import get_embedder
    _embedder = get_embedder()
except Exception as _emb_err:
    _log.warning("embedding model unavailable (%s); nodes will be stored "
                 "without embeddings", _emb_err)
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
             description="Search the memory graph in THIS server's namespace: the query "
                         "finds seed nodes (embedding similarity when available, else "
                         "keyword), expands out through their graph edges, and returns the "
                         "connected nodes as JSON ranked by relevance and time-decayed "
                         "confidence (older/less-used memories score lower). Read-only; the "
                         "read is audited. This is the primary way to pull prior decisions/"
                         "context back into a session at the start of work. Isolated "
                         "namespaces never surface another repo's memory. Returns [] when "
                         "nothing matches.",
             inputSchema={"type": "object",
                          "properties": {"query": {"type": "string",
                                          "description": "What to recall, e.g. 'why did we "
                                          "drop the psycopg dependency'. Matched semantically "
                                          "if an embedder is loaded, otherwise by keyword."},
                                         "depth": {"type": "integer",
                                          "description": "How many graph hops to expand from "
                                          "the seed nodes. Defaults to 2."},
                                         "top_k": {"type": "integer",
                                          "description": "Max nodes to return after ranking. "
                                          "Defaults to 10."}},
                          "required": ["query"]}),
        Tool(name="memory.read",
             description="Fetch one memory node by its exact id and return it as JSON, or "
                         "'null' if no such node exists in this namespace. Reading touches "
                         "the node's access stats (recency/use), which affects its future "
                         "recall ranking and prune resistance. Use when memory.recall or "
                         "memory.expand has already given you a node id and you want its full "
                         "body. Read-only aside from the access-stat bump.",
             inputSchema={"type": "object",
                          "properties": {"node_id": {"type": "string",
                                          "description": "Node id as returned by recall/"
                                          "expand/write, e.g. 'mem-1a2b3c4d'."}},
                          "required": ["node_id"]}),
        Tool(name="memory.expand",
             description="Traverse the memory graph outward from one or more known node ids "
                         "up to `depth` hops and return the connected subgraph as JSON. Use "
                         "when you already have seed node ids (from recall/write) and want "
                         "their neighbours/related memories, optionally restricted to certain "
                         "edge relations. Read-only and namespace-scoped.",
             inputSchema={"type": "object",
                          "properties": {"seed_ids": {"type": "array",
                                          "items": {"type": "string"},
                                          "description": "Node ids to start from, e.g. "
                                          "['mem-1a2b3c4d','mem-9f8e7d6c'] (required)."},
                                         "depth": {"type": "integer",
                                          "description": "Max hops to walk from each seed. "
                                          "Defaults to 2."},
                                         "rels": {"type": "array",
                                          "items": {"type": "string"},
                                          "description": "Optional filter: only traverse these "
                                          "edge relations (e.g. ['DEPENDS_ON','SUPERSEDES']); "
                                          "omit to follow all relation types."}},
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
                                                  "enum": list(VALID_KINDS),
                                                  "description": "Top-level memory category; "
                                                  "must be consistent with node_kind (see the "
                                                  "tool description's mapping)."},
                                  "node_kind": {"type": "string",
                                                "enum": sorted(HALF_LIFE),
                                                "description": "Specific node kind, e.g. "
                                                "'decision' (365-day half-life, never "
                                                "auto-pruned) or 'investigation' (open item). "
                                                "Must match the chosen memory_type."},
                                  "name": {"type": "string",
                                           "description": "Short human-readable title for the "
                                           "node, e.g. 'Dropped psycopg for portability'."},
                                  "body": {"type": "object",
                                           "description": "Structured JSON payload with the "
                                           "memory's content/details, e.g. {\"summary\": "
                                           "\"...\", \"rationale\": \"...\"}."},
                                  "repo": {"type": "string",
                                           "description": "Optional originating repo name to "
                                           "tag the node with; defaults to the server's "
                                           "namespace context."},
                                  "confidence": {"type": "number",
                                                 "description": "Initial confidence 0.0-1.0 "
                                                 "(feeds time-decayed recall ranking). "
                                                 "Defaults to 1.0."}},
                              "required": ["memory_type", "node_kind", "name", "body"]}),
            Tool(name="memory.link",
                 description="Create a typed edge between two nodes in this "
                             "namespace. Both nodes must already exist in THIS "
                             "namespace (cross-namespace links are rejected). rel "
                             "must be one of the documented relations: RELATES_TO "
                             "(generic association), DEPENDS_ON, DECISION_ABOUT, "
                             "DISCOVERED_IN, SUPERSEDES, CONSOLIDATES.",
                 inputSchema={"type": "object",
                              "properties": {"src": {"type": "string",
                                              "description": "Source node id, e.g. "
                                              "'mem-1a2b3c4d'. Must already exist in this "
                                              "namespace."},
                                             "dst": {"type": "string",
                                              "description": "Destination node id, e.g. "
                                              "'mem-9f8e7d6c'. Must already exist in this "
                                              "namespace."},
                                             "rel": {"type": "string",
                                                     "enum": sorted(VALID_RELS),
                                              "description": "Edge relation type (see the "
                                              "tool description); direction is src -> dst."},
                                             "weight": {"type": "number",
                                              "description": "Optional edge strength/weight. "
                                              "Defaults to 1.0."}},
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
                    # embedding failure just drops recall to keyword-only; log it
                    # so a broken embedder doesn't silently degrade every recall.
                    _log.warning("query embedding failed; recall will be "
                                 "keyword-only for this query", exc_info=True)
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
