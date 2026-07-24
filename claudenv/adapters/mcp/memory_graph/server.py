"""
claude-env :: Adapters - Memory Graph MCP Server

Memory operations: store, retrieve, search, link, expand, session ingest.
Thin adapter over IMemoryService; logic in domain/memory/service.py.
"""
from __future__ import annotations

import json
import logging
import os

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from claudenv.adapters.logging import configure_logging
from claudenv.adapters.mcp._deps import build_deps
from claudenv.di import get_container
from claudenv.domain.memory import EdgeRelation, MemoryType, NodeKind
from claudenv.domain.value_objects import NodeId, RepoSlug
from claudenv.ports import IMemoryService

logger = logging.getLogger(__name__)


class MemoryGraphServer:
    """Memory graph MCP server."""

    def __init__(
            self,
            repo: RepoSlug,
            memory_service: IMemoryService,
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
                    description=(
                        "Store a new memory node in the persistent knowledge graph. Memories persist "
                        "across sessions and are retrieved via semantic search or graph traversal. "
                        "Returns the created node_id. Use for storing decisions, architectural "
                        "conventions, investigation findings, entity definitions, and any knowledge "
                        "that should be recallable in future sessions."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "content": {
                                "type": "string",
                                "description": "The content body of the memory. Free-form text that "
                                               "will be indexed for semantic search via embeddings.",
                            },
                            "type": {
                                "type": "string",
                                "enum": ["episodic", "semantic", "procedural", "agent"],
                                "default": "episodic",
                                "description": "Memory category: episodic (specific past experience), "
                                               "semantic (factual knowledge), procedural (how-to workflows), "
                                               "agent (agent-specific namespace).",
                            },
                            "kind": {
                                "type": "string",
                                "enum": ["session", "decision", "investigation", "entity", "concept",
                                         "architecture", "preference", "workflow", "convention", "pattern"],
                                "default": "session",
                                "description": "Specific node sub-type within the memory category. "
                                               "Decisions and architecture nodes are never pruned automatically.",
                            },
                            "name": {
                                "type": "string",
                                "description": "Human-readable short name for this memory (e.g. "
                                               "'auth-refactor-decision'). Displayed in search results.",
                            },
                            "metadata": {
                                "type": "object",
                                "description": "Optional arbitrary key-value metadata to attach to "
                                               "the node (e.g. {\"file\": \"auth.py\", \"commit\": \"abc123\"}).",
                            },
                        },
                        "required": ["content"],
                    },
                ),
                Tool(
                    name="memory.retrieve",
                    description=(
                        "Retrieve a single memory node by its node_id. Returns the full node with "
                        "all properties (name, content, type, kind, confidence, timestamps). "
                        "Use after memory.store or memory.search to get the complete record."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "node_id": {
                                "type": "string",
                                "description": "UUID of the memory node to retrieve. Obtained from "
                                               "the return value of memory.store or from memory.search "
                                               "results.",
                            },
                        },
                        "required": ["node_id"],
                    },
                ),
                Tool(
                    name="memory.search",
                    description=(
                        "Search persistent memory by natural language query. Combines keyword "
                        "matching with embedding-based semantic similarity for ranked results. "
                        "Optionally performs graph traversal (configurable depth) to include "
                        "connected nodes. Returns ranked nodes with scores. Use types filter "
                        "to narrow to specific memory categories."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Natural language search query describing what "
                                               "you're looking for. The system uses both keyword "
                                               "matching and semantic embedding similarity.",
                            },
                            "depth": {
                                "type": "integer",
                                "default": 2,
                                "description": "How many levels of graph connections to traverse "
                                               "from matching nodes. 0 = no traversal (flat search). "
                                               "Higher values find more contextually related memories "
                                               "but may include noise.",
                            },
                            "top_k": {
                                "type": "integer",
                                "default": 10,
                                "description": "Maximum number of result nodes to return.",
                            },
                            "types": {
                                "type": "array",
                                "items": {"type": "string",
                                          "enum": ["episodic", "semantic", "procedural", "agent"]},
                                "description": "Optional filter to only return nodes of specific "
                                               "memory types. Omit to search all types.",
                            },
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="memory.link",
                    description=(
                        "Create a directed edge (relationship) between two existing memory nodes. "
                        "Edges form the memory graph structure used by memory.search for traversal. "
                        "Relations describe how nodes are connected. Returns the created edge_id."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "source": {
                                "type": "string",
                                "description": "node_id of the source node (the subject of the relation).",
                            },
                            "target": {
                                "type": "string",
                                "description": "node_id of the target node (the object of the relation).",
                            },
                            "relation": {
                                "type": "string",
                                "enum": ["relates_to", "depends_on", "decision_about", "discovered_in",
                                         "supersedes", "consolidates"],
                                "description": "The type of relationship between the nodes. "
                                               "relates_to=general association, depends_on=prerequisite, "
                                               "decision_about=this node documents a decision about target, "
                                               "discovered_in=found during investigation of target, "
                                               "supersedes=replaces a previous node, consolidates=merge result.",
                            },
                            "weight": {
                                "type": "number",
                                "default": 1.0,
                                "description": "Strength of the relationship (0.0 to 1.0). "
                                               "Higher weights influence traversal ranking.",
                            },
                        },
                        "required": ["source", "target", "relation"],
                    },
                ),
                Tool(
                    name="memory.expand",
                    description=(
                        "Starting from one or more seed nodes, traverse outgoing edges to find "
                        "connected memories. Optionally filter by relation type. Returns all "
                        "nodes reachable within the given depth. Use after memory.search or "
                        "memory.retrieve to explore the graph around a result."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "seed_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "One or more node_ids to use as starting points "
                                               "for graph expansion.",
                            },
                            "depth": {
                                "type": "integer",
                                "default": 2,
                                "description": "How many edge hops to traverse from the seeds. "
                                               "A depth of 1 returns direct neighbors only.",
                            },
                            "relations": {
                                "type": "array",
                                "items": {"type": "string",
                                          "enum": ["relates_to", "depends_on", "decision_about",
                                                   "discovered_in", "supersedes", "consolidates"]},
                                "description": "Optional filter: only follow edges of these "
                                               "relation types. Omit to follow all relations.",
                            },
                        },
                        "required": ["seed_ids"],
                    },
                ),
                Tool(
                    name="memory.session_ingest",
                    description=(
                        "Ingest a session's conversation history into episodic memory. Creates "
                        "a structured memory node from the session transcript that can later be "
                        "recalled via memory.search. Use at end of session or when a meaningful "
                        "interaction should be preserved for future reference."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "session_id": {
                                "type": "string",
                                "description": "Unique identifier for this session (e.g. Claude Code "
                                               "session UUID). Used for deduplication.",
                            },
                            "messages": {
                                "type": "array",
                                "items": {"type": "object"},
                                "description": "Array of conversation messages to ingest. Each "
                                               "message should have at minimum a 'role' and 'content' "
                                               "field. The system extracts topics, files touched, "
                                               "and outcomes automatically.",
                            },
                        },
                        "required": ["session_id", "messages"],
                    },
                ),
                Tool(
                    name="memory.analytics",
                    description=(
                        "Get analytics and statistics about the current memory graph namespace. "
                        "Returns: total node count broken down by memory type and node kind, "
                        "edge type distribution, confidence score distribution, and namespace "
                        "information. Useful for understanding the scope and health of memory."
                    ),
                    inputSchema={
                        "type": "object",
                        "properties": {},
                    },
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
                logger.exception("memory tool error")
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
        # EdgeRelation's enum values are UPPER_SNAKE_CASE (e.g. RELATES_TO) but
        # the tool schema advertises lowercase relation names, so every
        # schema-valid call used to raise ValueError building the enum.
        relation = EdgeRelation(args["relation"].upper())
        weight = args.get("weight", 1.0)

        graph = self.memory.create_graph(f"proj-{self.repo}", isolated=True)
        edge_id = graph.add_edge(source, target, relation.value, weight)

        self.audit.tool_call(
            tool="memory.link",
            args={"source": str(source), "target": str(target), "relation": relation.value},
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
            relations=[EdgeRelation(r.upper()) for r in relations] if relations else None,
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
    deps = build_deps(os.getcwd(), session_id, actor)
    memory_service = get_container().get(IMemoryService)
    return MemoryGraphServer(repo_slug, memory_service, deps.audit_logger, deps.session_id)


async def main() -> None:
    logger.info("memory graph MCP server starting")
    configure_logging(console=False)
    repo_slug = os.environ.get("CLAUDE_ENV_REPO_NAME", "default")
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-memory")

    server = create_server(repo_slug, session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
