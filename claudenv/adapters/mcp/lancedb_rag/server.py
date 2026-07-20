"""
claude-env :: Adapters - LanceDB RAG MCP Server

Semantic search over indexed code repositories.
Thin adapter over RagService; all logic in application/rag.py.

Every retrieval is fail-closed against the shared incident kill-switch
(claudenv.domain.incident.is_incident_active): when incident mode is active,
all tool handlers are denied before any data access.

Results are poison/injection-screened and wrapped in <retrieved_context>
data delimiters before being returned, so retrieved text can never act as an
instruction to the agent. Three detectors run on each chunk
(claudenv.domain.security):
  * RagPoisonDetector       -> blocks chunks above the poison threshold (denied)
  * PromptInjectionDetector -> blocks instruction-like text above threshold (denied)
  * SecretDetector          -> redacts credential patterns before delivery (flagged)
"""
from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.config import get_config
from claudenv.adapters.persistence import SQLiteDatabase, SQLiteRagBookkeeping
from claudenv.adapters.vector.lancedb import LanceDbVectorStore
from claudenv.application.rag import RagIndexer, RagService
from claudenv.domain.incident import is_incident_active
from claudenv.domain.policy import PolicyService
from claudenv.domain.security import (
    PromptInjectionDetector,
    RagPoisonDetector,
    SecretDetector,
)
from claudenv.domain.value_objects import BranchName, RepoSlug, SessionId


class LanceDbRagServer:
    """LanceDB RAG MCP server - semantic search and retrieval."""

    def __init__(
            self,
            repo_slug: RepoSlug,
            branch: BranchName,
            rag_service: RagService,
            audit_logger,
            repo_root: Path,
            session_id: str = "mcp-rag",
    ):
        self.repo_slug = repo_slug
        self.branch = branch
        self.rag = rag_service
        self.audit = audit_logger
        self.repo_root = repo_root
        self.session_id = session_id
        self._poison = RagPoisonDetector()
        self._injection = PromptInjectionDetector()
        self._secret = SecretDetector()
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
            return await self._handle_tool(name, arguments)

    async def _handle_tool(self, name: str, arguments: dict) -> list[TextContent]:
        # Fail closed: during an active incident, deny every RAG tool before
        # any data access. Mirrors the policy engine's incident kill-switch.
        if is_incident_active():
            self.audit.security_event(
                category="rag_denied_incident", severity="high",
                detail=f"RAG tool '{name}' denied: incident mode active",
                source=name,
            )
            return [TextContent(
                type="text",
                text=f"ERROR: incident mode active — RAG operations denied ({name})",
            )]
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

        try:
            results = self.rag.search(
                repo=self.repo_slug,
                branch=self.branch,
                query=query,
                top_k=top_k,
                mode=mode,
            )
        except Exception as e:
            return [TextContent(type="text", text=f"ERROR: retrieval unavailable ({e})")]

        if file_filter:
            results = [r for r in results if fnmatch.fnmatch(r.chunk.file_path, file_filter)]

        display, denied, redacted, flagged = self._screen_results(results)

        self.audit.tool_call(
            tool="rag.search",
            args={
                "query": query, "top_k": top_k, "mode": mode,
                "screened_out": denied, "redacted": redacted, "flagged": flagged,
            },
            result_kind="ok",
        )

        if not display:
            return [TextContent(type="text", text="<retrieved_context/> (no safe results)")]

        lines = [f"Found {len(display)} results for: {query}"]
        if denied or redacted or flagged:
            lines.append(
                f"[screening] denied={denied} redacted={redacted} flagged={flagged}")
        lines.append("")
        for r, text, is_flagged in display:
            chunk = r.chunk
            tag = " [FLAGGED]" if is_flagged else ""
            lines.append(
                f"- [{r.score:.3f}]{tag} {chunk.file_path}:{chunk.start_line}-{chunk.end_line} "
                f"({chunk.symbol_type.value}:{chunk.symbol_name})")
            lines.append(f"  {text[:300]}...")
            lines.append("")

        return [TextContent(
            type="text",
            text="<retrieved_context>\n" + "\n".join(lines) + "\n</retrieved_context>")]

    def _screen_results(self, results: list) -> tuple[list, int, int, int]:
        """Screen retrieved chunks before they reach the agent.

        Returns ``(display, denied, redacted, flagged)`` where ``display`` is a
        list of ``(RetrievalResult, safe_text, is_flagged)`` tuples:

        * poison / prompt-injection hits above the block threshold are DENIED
          (dropped entirely, never delivered);
        * sub-threshold poison/injection hits are still delivered but FLAGGED
          so the agent treats them as suspect data;
        * secret patterns are REDACTED from the delivered text.
        """
        display: list = []
        denied = redacted = flagged = 0
        for r in results:
            chunk = r.chunk
            source = f"{self.repo_slug}@{self.branch}:{chunk.file_path}"
            text = chunk.text

            poison = self._poison.scan_chunk(text, source)
            inj = self._injection.scan(text, source)
            if poison.blocked or inj.blocked:
                denied += 1
                self.audit.security_event(
                    category="rag_result_denied",
                    severity="high",
                    detail=f"denied chunk from {source}: poison={poison.reasons} injection={inj.reasons}",
                    source=source,
                )
                continue

            is_flagged = poison.flagged or inj.flagged
            if is_flagged:
                flagged += 1

            secret = self._secret.scan(text, source)
            if secret.flagged:
                text = self._secret.redact(text)
                redacted += 1
                flagged += 1
                self.audit.security_event(
                    category="rag_secret_redacted",
                    severity="medium",
                    detail=f"redacted {secret.reasons} in chunk from {source}",
                    source=source,
                )

            display.append((r, text, is_flagged))
        return display, denied, redacted, flagged

    async def _do_index(self, args: dict) -> list[TextContent]:
        force_full = args.get("force_full", False)

        # index_repo() walks repo_root and calls RagIndexer.index_file() per
        # file, which already skips unchanged files via content-hash
        # bookkeeping -- so a plain walk is naturally incremental. force_full
        # is accepted for API compatibility but doesn't currently bypass that
        # skip (no force-overwrite path was implemented upstream either).
        result = self.rag.index_repo(self.repo_slug, self.branch, self.repo_root)

        self.audit.tool_call(
            tool="rag.index",
            args={"force_full": force_full},
            result_kind="ok",
        )

        return [TextContent(type="text", text=f"Indexing complete: {result}")]

    async def _do_status(self) -> list[TextContent]:
        state = self.rag.get_index_state(self.repo_slug, self.branch)

        if not state:
            return [TextContent(type="text", text="No index found for this repo+branch.")]

        return [TextContent(type="text", text=json.dumps({
            "repo": str(self.repo_slug),
            "branch": str(self.branch),
            "table_name": state.table_name,
            "last_commit": state.last_commit[:8] if state.last_commit else None,
            "chunk_count": state.chunk_count,
            "embed_model": state.embed_model,
            "updated_at": state.updated_at.isoformat(),
        }, indent=2))]

    async def _do_get_chunk(self, args: dict) -> list[TextContent]:
        chunk_id = args["chunk_id"]
        chunk = self.rag.get_chunk(self.repo_slug, self.branch, chunk_id)

        if not chunk:
            return [TextContent(type="text", text=f"Chunk not found: {chunk_id}")]

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
        repo_root: str | Path | None = None,
        session_id: str = "mcp-rag",
        actor: str = "lancedb-rag-mcp",
) -> LanceDbRagServer:
    repo_slug_v = RepoSlug.from_string(repo_slug)
    branch_v = BranchName.from_string(branch)
    config = get_config()

    # IConfigProvider has no get_rag_service()/get_audit_logger() -- those
    # methods never existed on ConfigProvider (see claudenv/ports/config.py).
    # Build the real adapters directly, the same way terminal/server.py's
    # create_server() does.
    root = Path(repo_root or os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
    policy_engine = PolicyService(config).load_engine(str(root))
    tier = policy_engine.get_compiled().tier

    db = SQLiteDatabase(config.get_database_dsn())
    rag_config = config.get_rag_config()

    bookkeeping = SQLiteRagBookkeeping(db)
    store = LanceDbVectorStore(config.get_lancedb_path(), rag_config.embedding_dim)

    from claudenv.adapters.embedding import get_embedder, get_reranker
    embedder = get_embedder(rag_config)
    reranker = get_reranker(rag_config)

    indexer = RagIndexer(
        repo=repo_slug_v, branch=branch_v, store=store,
        bookkeeping=bookkeeping, embedder=embedder, chunker=rag_config,
    )
    rag_service = RagService(indexer, store, embedder, reranker, bookkeeping=bookkeeping)

    audit_logger = SqliteAuditLogger(
        db=db,
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=repo_slug_v,
        tier=tier,
    )

    return LanceDbRagServer(repo_slug_v, branch_v, rag_service, audit_logger, root, session_id)


async def main() -> None:
    repo_slug = os.environ.get("CLAUDE_ENV_REPO_NAME", "default")
    branch = os.environ.get("CLAUDE_ENV_BRANCH", "main")
    session_id = os.environ.get("CLAUDE_ENV_SESSION", "mcp-rag")

    server = create_server(repo_slug, branch, session_id=session_id)

    async with stdio_server() as (read, write):
        await server.server.run(read, write, server.server.create_initialization_options())


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
