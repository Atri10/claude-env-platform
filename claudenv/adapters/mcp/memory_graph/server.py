"""
claude-env :: Adapters - Memory Graph MCP Server

Memory operations: store, retrieve, search, link, expand, session ingest.
Thin adapter over MemoryService; logic in application/memory.py.
"""
from __future__ import annotations

import json
import os
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pathlib import Path
from typing import Any

from claudenv.adapters.config import get_config
from claudenv.application.memory import MemoryService
from claudenv.domain.memory import MemoryType, NodeKind, EdgeRelation
from claudenv.domain.value_objects import RepoSlug, Tier, SessionId, NodeId


class MemoryGraphServer:
    """Memory graph MCP server."""

    def __init__(
            self,
            repo: RepoSlug,
            memory_service: MemoryService,
            audit_logger,
            session_id: str = "mcp-memory",
    ):
        self.repo = repo
        self.memory = memory_service
        self.audit = audit_logger
        self.session_id = session_id
        self.server = Server("memory-graph")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="memory.store",
                    description="Store a memory node. Returns node_id.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "content": {"type": "string", "description": "Memory content"},
                            "type": {"type": "string", "enum": ["episodic", "semantic", "procedural", "agent"],
                                     "default": "episodic"},
                            "kind": {"type": "string",
                                     "enum": ["session", "decision", "investigation", "entity", "concept",
                                              "architecture", "preference", "workflow", "convention", "pattern"],
                                     "default": "session"},
                            "name": {"type": "string", "description": "Human-readable name"},
                            "metadata": {"type": "object", "description": "Additional metadata"},
                        },
                        "required": ["content"],
                    },
                ),
                Tool(
                    name="memory.retrieve",
                    description="Retrieve a memory node by ID.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "node_id": {"type": "string", "description": "Node identifier"},
                        },
                        "required": ["node_id"],
                    },
                ),
                Tool(
                    name="memory.search",
                    description="Search memory by query (keyword + embedding). Returns ranked nodes with graph expansion.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query"},
                            "depth": {"type": "integer", "default": 2, "description": "Graph traversal depth"},
                            "top_k": {"type": "integer", "default": 10, "description": "Max results"},
                            "types": {"type": "array", "items": {"type": "string",
                                                                 "enum": ["episodic", "semantic", "procedural",
                                                                          "agent"]}},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="memory.link",
                    description="Create an edge between two nodes.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source": {"type": "string", "description": "Source node ID"},
                            "target": {"type": "string", "description": "Target node ID"},
                            "relation": {"type": "string",
                                         "enum": ["relates_to", "depends_on", "decision_about", "discovered_in",
                                                  "supersedes", "consolidates"]},
                            "weight": {"type": "number", "default": 1.0},
                        },
                        "required": ["source", "target", "relation"],
                    },
                ),
                Tool(
                    name="memory.expand",
                    description="Expand graph from seed nodes via edges.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "seed_ids": {"type": "array", "items": {"type": "string"}, "description": "Seed node IDs"},
                            "depth": {"type": "integer", "default": 2, "description": "Traversal depth"},
                            "relations": {"type": "array", "items": {"type": "string",
                                                                     "enum": ["relates_to", "depends_on",
                                                                              "decision_about", "discovered_in",
                                                                              "supersedes", "consolidates"]}},
                        },
                        "required": ["seed_ids"],
                    },
                ),
                Tool(
                    name="memory.session_ingest",
                    description="Ingest session conversation into episodic memory.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_id": {"type": "string", "description": "Session identifier"},
                            "messages": {"type": "array", "items": {"type": "object"},
                                         "description": "Conversation messages"},
                        },
                        "required": ["session_id", "messages"],
                    },
                ),
                Tool(
                    name="memory.analytics",
                    description="Get memory graph analytics (node counts, edge types, confidence distribution).",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "memory.store":
                    return await self._do_store(arguments)
                if name == "memory.retrieve":
                    return await self._do_retrieve(arguments)
                if name == "memory.search":
                    return await self._do_search(arguments)
                if name == "memory.link":
                    return await self._do_link(arguments)
                if name == "memory.expand":
                    return await self._do_expand(arguments)
                if name == "memory.session_ingest":
                    return await self._do_session_ingest(arguments)
                if name == "memory.analytics":
                    return await self._do_analytics()

                return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]
            except Exception as e:
                self.audit.security_event(
                    category="memory_error", severity="medium",
                    detail=str(e), source=name,
                )
                return [TextContent(type="text", text=f"ERROR: {e}")]

    async def _do_store(self, args: dict) -> list[TextContent]:
        content = args["content"]
        mem_type = MemoryType(args.get("type", "episodic"))
        kind = NodeKind(args.get("kind", "session"))
        name = args.get("name", content[:50])
        metadata = args.get("metadata", {})

        graph = self.memory.create_graph(f"proj-{self.repo}", isolated=True)
        node_id = graph.add_node(
            memory_type=mem_type,
            node_kind=kind,
            name=name,
            body={"content": content, **metadata},
            repo=str(self.repo),
        )

        self.audit.tool_call(
            tool="memory.store",
            args={"type": mem_type.value, "kind": kind.value, "name": name},
            result_kind="ok",
        )

        return [TextContent(type="text", text=f"Stored: {node_id}")]

    async def _do_retrieve(self, args: dict) -> list[TextContent]:
        node_id = NodeId.from_string(args["node_id"])
        graph = self.memory.create_graph(f"proj-{self.repo}", isolated=True)
        node = graph.get_node(node_id)

        if not node:
            return [TextContent(type="text", text=f"Node not found: {node_id}")]

        return [TextContent(type="text", text=json.dumps({
            "node_id": str(node.node_id),
            "namespace": node.namespace,
            "type": node.memory_type.value,
            "kind": node.node_kind.value,
            "name": node.name,
            "content": node.body.get("content", ""),
            "metadata": {k: v for k, v in node.body.items() if k != "content"},
            "confidence": node.confidence,
            "effective_confidence": node.effective_confidence,
            "created_at": node.created_at.isoformat(),
            "updated_at": node.updated_at.isoformat(),
            "access_count": node.access_count,
            "superseded_by": node.superseded_by,
        }, indent=2))]

    async def _do_search(self, args: dict) -> list[TextContent]:
        query = args["query"]
        depth = args.get("depth", 2)
        top_k = args.get("top_k", 10)
        types = args.get("types")

        mem_type = None
        if types:
            mem_type = MemoryType(types[0])

        graph = self.memory.create_graph(f"proj-{self.repo}", isolated=True)
        results = graph.recall(
            query=query,
            depth=depth,
            top_k=top_k,
            memory_type=mem_type,
        )

        if not results:
            return [TextContent(type="text", text=f"No results for: {query}")]

        lines = [f"Found {len(results)} results for: {query}\n"]
        for n in results:
            lines.append(f"- [{n.effective_confidence:.2f}] {n.name} ({n.node_kind.value})")
            lines.append(f"  ID: {n.node_id}")
            lines.append(f"  Type: {n.memory_type.value}")
            lines.append(f"  Content: {n.body.get('content', '')[:200]}...")
            lines.append("")

        return [TextContent(type="text", text="\n".join(lines))]

    async def _do_link(self, args: dict) -> list[TextContent]:
        source = NodeId.from_string(args["source"])
        target = NodeId.from_string(args["target"])
        relation = EdgeRelation(args["relation"])
        weight = args.get("weight", 1.0)

        graph = self.memory.create_graph(f"proj-{self.repo}", isolated=True)
        edge_id = graph.add_edge(source, target, relation.value, weight)

        self.audit.tool_call(
            tool="memory.link",
            args={"source": source, "target": target, "relation": relation.value},
            result_kind="ok",
        )

        return [TextContent(type="text", text=f"Linked: {edge_id}")]

    async def _do_expand(self, args: dict) -> list[TextContent]:
        seed_ids = [NodeId.from_string(s) for s in args["seed_ids"]]
        depth = args.get("depth", 2)
        relations = args.get("relations")

        graph = self.memory.create_graph(f"proj-{self.repo}", isolated=True)
        nodes = graph.expand(
            seed_ids=seed_ids,
            depth=depth,
            relations=[EdgeRelation(r) for r in relations] if relations else None,
        )

        return [TextContent(type="text", text=json.dumps([{
            "node_id": str(n.node_id),
            "name": n.name,
            "type": n.memory_type.value,
            "kind": n.node_kind.value,
            "confidence": n.effective_confidence,
        } for n in nodes], indent=2))]

    async def _do_session_ingest(self, args: dict) -> list[TextContent]:
        session_id = args["session_id"]
        messages = args["messages"]

        graph = self.memory.create_graph(f"session:{session_id}", isolated=True)
        for i, msg in enumerate(messages):
            graph.add_node(
                memory_type=MemoryType.EPISODIC,
                node_kind=NodeKind.SESSION,
                name=f"msg_{i}",
                body={"role": msg.get("role", "user"), "content": msg.get("content", "")},
            )

        return [TextContent(type="text", text=f"Ingested {len(messages)} messages into session:{session_id}")]

    async def _do_analytics(self) -> list[TextContent]:
        graph = self.memory.create_graph(f"proj-{self.repo}", isolated=True)

        # Get all nodes
        nodes = graph.list_nodes(limit=10000)

        counts = {"total": len(nodes)}
        for mt in MemoryType:
            counts[mt.value] = sum(1 for n in nodes if n.memory_type == mt)
        for nk in NodeKind:
            counts[nk.value] = sum(1 for n in nodes if n.node_kind == nk)

        return [TextContent(type="text", text=json.dumps({
            "namespace": f"proj-{self.repo}",
            "counts": counts,
        }, indent=2))]


def create_server(
        repo_slug: str,
        session_id: str = "mcp-memory",
        actor: str = "memory-graph-mcp",
) -> MemoryGraphServer:
    repo_slug = RepoSlug.from_string(repo_slug)
    config = get_config()

    memory_service = config.get_memory_service()

    audit_logger = config.get_audit_logger(
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=repo_slug,
        tier=Tier.INTERNAL,
    )

    return MemoryGraphServer(repo_slug, memory_service, audit_logger, session_id)


async def main() -> None:
    repo_slug = os.environ.get("CLAUDE_ENV_REPO_NAME", "default")
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-memory")

    server = create_server(repo_slug, session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    import os

    asyncio.run(main())
