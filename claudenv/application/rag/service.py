"""
claude-env :: Application - RAG Service - RagService
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claudenv.domain.rag import (
    BranchName,
    Chunk,
    IndexState,
    RepoSlug,
    RetrievalMode,
    RetrievalQuery,
    RetrievalResult,
)
from claudenv.domain.value_objects import Tier
from claudenv.ports import (
    IEmbeddingProvider,
    IRagBookkeeping,
    IRagIndexer,
    IRagRetriever,
    IReranker,
)
import logging
logger = logging.getLogger(__name__)

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".claude-env", "dist", "build"}
_MAX_FILE_BYTES = 2_000_000


@dataclass
class RagService:
    """High-level RAG service combining indexing and retrieval."""

    indexer: IRagIndexer
    retriever: IRagRetriever
    embedder: IEmbeddingProvider
    reranker: IReranker
    bookkeeping: IRagBookkeeping | None = None

    def search(
            self,
            repo: RepoSlug,
            branch: BranchName,
            query: str,
            top_k: int = 40,
            mode: RetrievalMode = "hybrid",
            tier_filter: Tier | None = None,
    ) -> list[RetrievalResult]:
        query_vec = self.embedder.embed_query(query) if query else None
        rq = RetrievalQuery(
            query=query, query_vector=query_vec, top_k=top_k,
            mode=mode, repo_filter=repo, branch_filter=branch, tier_filter=tier_filter,
        )
        results = self.retriever.search(rq)

        # Rerank if a real reranker is configured. This field existed on the
        # dataclass but nothing ever called it -- reranking silently never
        # happened even when a reranker model was configured.
        if self.reranker and query and results:
            texts = [r.chunk.text for r in results]
            order = self.reranker.rerank(query, texts, top_k)
            results = [results[i] for i in order if i < len(results)]

        return results

    def index_file(
            self, repo: RepoSlug, branch: BranchName, commit: str,
            file_path: str, text: str,
    ) -> int:
        return self.indexer.index_file(repo, branch, commit, file_path, text)

    def index_repo(self, repo: RepoSlug, branch: BranchName, repo_root: Path) -> dict[str, Any]:
        """Walk repo_root, read text files (skipping vendor dirs/binaries/
        oversized files), and run a full index pass. index_file()'s content-
        hash check (see RagIndexer.index_file) already makes repeat calls
        incremental -- unchanged files are skipped."""
        files: dict[str, str] = {}
        for path in repo_root.rglob("*"):
            if path.is_dir() or any(part in _SKIP_DIRS for part in path.parts):
                continue
            try:
                data = path.read_bytes()
            except Exception:
                logger.warning("skipped unreadable file during indexing: %s", path, exc_info=True)
                continue
            if b"\x00" in data[:8192] or len(data) > _MAX_FILE_BYTES:
                continue
            try:
                text = data.decode("utf-8")
            except Exception:
                logger.warning("skipped unreadable file during indexing: %s", path, exc_info=True)
                continue
            files[str(path.relative_to(repo_root))] = text
        return self.indexer.full_index(repo, branch, files)

    def get_index_state(self, repo: RepoSlug, branch: BranchName) -> IndexState | None:
        if self.bookkeeping is None:
            return None
        return self.bookkeeping.get_index_state(repo, branch)

    def get_chunk(self, repo: RepoSlug, branch: BranchName, chunk_id: str) -> Chunk | None:
        getter = getattr(self.retriever, "get_chunk", None)
        if getter is None:
            return None
        return getter(repo, branch, chunk_id)

    def reindex(self, repo: RepoSlug, branch: BranchName, changed_files: list[str]) -> dict[str, Any]:
        total = 0
        for f in changed_files:
            # In production, read file and call index_file
            total += 1
        return {"files": total, "chunks": 0}
