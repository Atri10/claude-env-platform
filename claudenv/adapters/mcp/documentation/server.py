"""
claude-env :: Adapters - Documentation MCP Server

API schema, dependency listing, and reference lookup.
Thin adapter over documentation service; logic in application/docs.py.
"""
from __future__ import annotations

import json
import os
from claudenv.application.docs import DocsService
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool
from pathlib import Path
from typing import Any

from claudenv.adapters.config import get_config
from claudenv.domain.value_objects import RepoSlug, Tier, SessionId


class DocumentationServer:
    """Documentation lookup MCP server."""

    def __init__(
            self,
            repo: RepoSlug,
            docs_service: DocsService,
            audit_logger,
            session_id: str = "mcp-docs",
    ):
        self.repo = repo
        self.docs = docs_service
        self.audit = audit_logger
        self.session_id = session_id
        self.server = Server("documentation")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def list_tools() -> list[Tool]:
            return [
                Tool(
                    name="docs.list_deps",
                    description="List all declared dependencies with versions.",
                    inputSchema={"type": "object", "properties": {}},
                ),
                Tool(
                    name="docs.get_schema",
                    description="Get API schema (OpenAPI/Swagger) if available.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "format": {"type": "string", "enum": ["json", "yaml"], "default": "json"},
                        },
                    },
                ),
                Tool(
                    name="docs.find_reference",
                    description="Search for symbol/class/function documentation by name or pattern.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "Symbol name or pattern"},
                            "kind": {"type": "string", "enum": ["class", "function", "module", "variable", "any"],
                                     "default": "any"},
                        },
                        "required": ["query"],
                    },
                ),
                Tool(
                    name="docs.get_file",
                    description="Read a documentation/markdown file from the repo.",
                    inputSchema={
                        "type": "object",
                        "properties": {
                            "path": {"type": "string", "description": "Repo-relative path"},
                        },
                        "required": ["path"],
                    },
                ),
                Tool(
                    name="docs.get_readme",
                    description="Get the repository README content.",
                    inputSchema={"type": "object", "properties": {}},
                ),
            ]

        @self.server.call_tool()
        async def call_tool(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "docs.list_deps":
                    return await self._do_list_deps()
                if name == "docs.get_schema":
                    return await self._do_get_schema(arguments)
                if name == "docs.find_reference":
                    return await self._do_find_reference(arguments)
                if name == "docs.get_file":
                    return await self._do_get_file(arguments)
                if name == "docs.get_readme":
                    return await self._do_get_readme()

                return [TextContent(type="text", text=f"ERROR: unknown tool {name}")]
            except Exception as e:
                self.audit.security_event(
                    category="docs_error", severity="medium",
                    detail=str(e), source=name,
                )
                return [TextContent(type="text", text=f"ERROR: {e}")]

    async def _do_list_deps(self) -> list[TextContent]:
        deps = self.docs.list_dependencies()

        return [TextContent(type="text", text=json.dumps(deps, indent=2))]

    async def _do_get_schema(self, args: dict) -> list[TextContent]:
        fmt = args.get("format", "json")
        schema = self.docs.get_schema(fmt)

        if not schema:
            return [TextContent(type="text", text="No API schema found for this repo.")]

        return [TextContent(type="text", text=json.dumps(schema, indent=2) if fmt == "json" else schema)]

    async def _do_find_reference(self, args: dict) -> list[TextContent]:
        query = args["query"]
        kind = args.get("kind", "any")

        refs = self.docs.find_reference(query, kind)

        if not refs:
            return [TextContent(type="text", text=f"No references found for: {query}")]

        lines = [f"Found {len(refs)} references for: {query}\n"]
        for r in refs:
            lines.append(f"- {r['kind']}: {r['name']}")
            lines.append(f"  File: {r['file_path']}:{r['line']}")
            lines.append(f"  Signature: {r.get('signature', 'N/A')}")
            lines.append(f"  Doc: {r.get('doc', 'N/A')[:200]}...")
            lines.append("")

        return [TextContent(type="text", text="\n".join(lines))]

    async def _do_get_file(self, args: dict) -> list[TextContent]:
        path = args["path"]
        content = self.docs.get_file(path)

        if not content:
            return [TextContent(type="text", text=f"File not found: {path}")]

        return [TextContent(type="text", text=content)]

    async def _do_get_readme(self) -> list[TextContent]:
        content = self.docs.get_readme()

        if not content:
            return [TextContent(type="text", text="No README found.")]

        return [TextContent(type="text", text=content)]


def create_server(
        repo_slug: str,
        session_id: str = "mcp-docs",
        actor: str = "docs-mcp",
) -> DocumentationServer:
    repo_slug = RepoSlug.from_string(repo_slug)
    config = get_config()

    docs_service = config.get_docs_service()

    audit_logger = config.get_audit_logger(
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=repo_slug,
        tier=Tier.INTERNAL,
    )

    return DocumentationServer(repo_slug, docs_service, audit_logger, session_id)


async def main() -> None:
    repo_slug = os.environ.get("CLAUDE_ENV_REPO_NAME", "default")
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-docs")

    server = create_server(repo_slug, session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    import os

    asyncio.run(main())
