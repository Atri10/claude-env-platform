"""
claude-env :: Adapters - LanceDB RAG MCP Server

Semantic search over indexed code repositories.
Thin adapter over RagService; all logic in application/rag.py.
"""
from __future__ import annotations

import os
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pathlib import Path
from typing import Any

from claudenv.adapters.config import get_config
from claudenv.application.rag import RagService
from claudenv.domain.value_objects import RepoSlug, Tier, SessionId, BranchName


class LanceDbRagServer:
    """LanceDB RAG MCP server - semantic search and retrieval."""

    def __init__(
            self,
            repo_slug: RepoSlug,
            branch: BranchName,
            rag_service: RagService,
            audit_logger,
            session_id: str = "mcp-rag",
    ):
        self.repo_slug = repo_slug
        self.branch = branch
        self.rag = rag_service
        self.audit = audit_logger
        self.session_id = session_id
        self.server = Server("lancedb-rag")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="rag.search",
                    description="Semantic search over indexed repository code. Returns top-k chunks with scores and metadata.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Search query text"},
                            "top_k": {"type": "integer", "default": 10, "description": "Number of results"},
                            "mode": {"type": "string", "enum": ["vector", "fts", "hybrid"], "default": "hybrid",
                                     "description": "Search mode"},
                            "file_filter": {"type": "string",
                                            "description": "Glob pattern to filter files (e.g. '*.py')"},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="rag.index",
                    description="Trigger incremental indexing of repository. Returns indexing stats (new/updated/deleted chunks).",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "force_full": {"type": "boolean", "default": False, "description": "Force full re-index"},
                        },
                    },
                ),
                Tool(
                    name="rag.index_status",
                    description="Get current index state (table name, commit, chunk count, last updated).",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="rag.get_chunk",
                    description="Retrieve a specific chunk by its ID.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "chunk_id": {"type": "string", "description": "Chunk identifier"},
                        },
                        "required": ["chunk_id"],
                    },
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "rag.search":
                    return await self._do_search(arguments)
                if name == "rag.index":
                    return await self._do_index(arguments)
                if name == "rag.index_status":
                    return await self._do_status()
                if name == "rag.get_chunk":
                    return await self._do_get_chunk(arguments)

                return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]
            except Exception as e:
                self.audit.security_event(
                    category="rag_error", severity="medium",
                    detail=str(e), source=name,
                )
                return [TextContent(type="text", text=f"ERROR: {e}")]

    async def _do_search(self, args: dict) -> list[TextContent]:
        query = args["query"]
        top_k = args.get("top_k", 10)
        mode = args.get("mode", "hybrid")
        file_filter = args.get("file_filter")

        results = self.rag.search(
            query=query,
            top_k=top_k,
            mode=mode,
            file_filter=file_filter,
        )

        if not results:
            return [TextContent(type="text", text=f"No results for: {query}")]

        lines = [f"Found {len(results)} results for: {query}\n"]
        for r in results:
            chunk = r.chunk
            lines.append(
                f"- [{r.score:.3f}] {chunk.file_path}:{chunk.start_line}-{chunk.end_line} ({chunk.symbol_type.value}:{chunk.symbol_name})")
            lines.append(f"  {chunk.text[:300]}...")
            lines.append("")

        self.audit.tool_call(
            tool="rag.search",
            args={"query": query, "top_k": top_k, "mode": mode},
            result_kind="ok",
        )

        return [TextContent(type="text", text="\n".join(lines))]

    async def _do_index(self, args: dict) -> list[TextContent]:
        force_full = args.get("force_full", False)

        if force_full:
            result = self.rag.index_repo()
        else:
            result = self.rag.index_path(str(self.repo_root))

        self.audit.tool_call(
            tool="rag.index",
            args={"force_full": force_full},
            result_kind="ok",
        )

        return [TextContent(type="text", text=f"Indexing complete: {result}")]

    async def _do_status(self) -> list[TextContent]:
        state = self.rag.get_index_state()

        if not state:
            return [TextContent(type="text", text="No index found for this repo+branch.")]

        import json
        return [TextContent(type="text", text=json.dumps({
            "repo": str(self.repo_slug),
            "branch": str(self.branch),
            "table_name": state.table_name,
            "last_commit": state.last_commit[:8],
            "chunk_count": state.chunk_count,
            "embed_model": state.embed_model,
            "updated_at": state.updated_at.isoformat(),
        }, indent=2))]

    async def _do_get_chunk(self, args: dict) -> list[TextContent]:
        chunk_id = args["chunk_id"]
        chunk = self.rag.get_chunk(chunk_id)

        if not chunk:
            return [TextContent(type="text", text=f"Chunk not found: {chunk_id}")]

        import json
        return [TextContent(type="text", text=json.dumps({
            "chunk_id": str(chunk.chunk_id),
            "repo": str(chunk.repo),
            "branch": str(chunk.branch),
            "commit": chunk.commit_sha[:8],
            "file_path": chunk.file_path,
            "file_type": chunk.file_type,
            "symbol_type": chunk.symbol_type.value,
            "symbol_name": chunk.symbol_name,
            "lines": f"{chunk.start_line}-{chunk.end_line}",
            "text": chunk.text,
            "tier": int(chunk.tier),
        }, indent=2))]


def create_server(
        repo_slug: str,
        branch: str = "main",
        session_id: str = "mcp-rag",
        actor: str = "lancedb-rag-mcp",
) -> LanceDbRagServer:
    repo_slug = RepoSlug.from_string(repo_slug)
    branch = BranchName.from_string(branch)
    config = get_config()

    rag_service = config.get_rag_service(repo_slug, branch)

    audit_logger = config.get_audit_logger(
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=repo_slug,
        tier=Tier.INTERNAL,
    )

    return LanceDbRagServer(repo_slug, branch, rag_service, audit_logger, session_id)


async def main() -> None:
    repo_slug = os.environ.get("CLAUDE_ENV_REPO_NAME", "default")
    branch = os.environ.get("CLAUDE_ENV_BRANCH", "main")
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-rag")

    server = create_server(repo_slug, branch, session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
