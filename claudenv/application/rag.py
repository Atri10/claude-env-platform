"""
claude-env :: Application - RAG Service
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from claudenv.domain.rag import (
    BranchName, Chunk, IndexState, RetrievalMode,
    RetrievalQuery, RetrievalResult, RepoSlug, RAGConfig,
)
from claudenv.domain.rag_chunker import ChunkerFactory, ChunkingConfig, make_chunker_factory
from claudenv.domain.value_objects import ContentHash, Tier
from claudenv.ports import (
    IEmbeddingProvider, IRagBookkeeping, IRagIndexer,
    IRagRetriever, IReranker,
)

_SKIP_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", ".claude-env", "dist", "build"}
_MAX_FILE_BYTES = 2_000_000


class RagIndexer(IRagIndexer):
    """Application service for indexing repositories."""

    def __init__(
            self,
            repo: RepoSlug,
            branch: BranchName,
            store: IRagRetriever,  # Using retriever interface for upsert
            bookkeeping: IRagBookkeeping,
            embedder: IEmbeddingProvider,
            chunker: RAGConfig | ChunkerFactory,
    ):
        self.repo = repo
        self.branch = branch
        self.store = store
        self.bookkeeping = bookkeeping
        self.embedder = embedder
        self.chunker = chunker
        # `chunker` is the RAG config in production wiring (DI passes the whole
        # RAGConfig), but a pre-built ChunkerFactory is accepted directly too
        # (e.g. from tests) so callers don't have to fake a full RAGConfig.
        if isinstance(chunker, ChunkerFactory):
            self._chunker_factory = chunker
        else:
            self._chunker_factory = make_chunker_factory(ChunkingConfig(
                chunk_target_tokens=chunker.chunk_target_tokens,
                chunk_overlap_tokens=chunker.chunk_overlap_tokens,
            ))

    def index_file(
            self, repo: RepoSlug, branch: BranchName, commit: str,
            file_path: str, text: str,
    ) -> int:
        chunks = self._chunk_file(file_path, text, repo, branch, commit)
        if not chunks:
            return 0

        content_hash = ContentHash.compute(text)
        prior = self.bookkeeping.get_file_hash(repo, branch, file_path)
        if prior and prior == content_hash:
            return 0

        texts = [c.text for c in chunks]
        vectors = self.embedder.embed_documents(texts)

        # Chunk is a frozen dataclass with no `embedding` field -- and
        # `chunk.embedding = vector` used to raise on it. LanceDB's schema
        # expects a "vector" column, which to_metadata() only emits from
        # self.metadata, so the vector has to travel through there.
        rows = []
        for chunk, vector in zip(chunks, vectors):
            row = chunk.to_metadata()
            row["vector"] = vector
            rows.append(row)

        self.store.upsert(self.repo, self.branch, rows)
        self.bookkeeping.set_file_hash(repo, branch, file_path, content_hash, len(rows))
        return len(rows)

    def index_batch(
            self, repo: RepoSlug, branch: BranchName, commit: str, chunks: list[Chunk],
    ) -> int:
        if not chunks:
            return 0

        texts = [c.text for c in chunks]
        vectors = self.embedder.embed_documents(texts)

        rows = []
        for chunk, vector in zip(chunks, vectors):
            row = chunk.to_metadata()
            row["vector"] = vector
            rows.append(row)

        self.store.upsert(repo, branch, rows)

        files = {}
        for c in chunks:
            files.setdefault(c.file_path, []).append(c)
        for path, file_chunks in files.items():
            content_hash = ContentHash.compute("\n".join(c.text for c in file_chunks))
            self.bookkeeping.set_file_hash(repo, branch, path, content_hash, len(file_chunks))

        return len(rows)

    def delete_file(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        self.store.delete_file(repo, branch, file_path)
        self.bookkeeping.delete_file_hash(repo, branch, file_path)

    def full_index(
            self, repo: RepoSlug, branch: BranchName, files: dict[str, str],
    ) -> dict[str, Any]:
        total_chunks = total_files = 0
        commit = "0"

        for path, text in files.items():
            n = self.index_file(repo, branch, commit, path, text)
            if n:
                total_files += 1
                total_chunks += n

        # IndexState is a frozen dataclass; IndexState.create() always sets
        # chunk_count=0, so `state.chunk_count = total_chunks` used to raise
        # dataclasses.FrozenInstanceError on every full_index() call.
        state = IndexState.create(
            repo=repo, branch=branch,
            table_name=str(RepoSlug(f"{repo}_{branch}")),
            commit=commit,
            model=self.embedder.model_name if hasattr(self.embedder, 'model_name') else "unknown",
        )
        state = replace(state, chunk_count=total_chunks)
        self.bookkeeping.set_index_state(state)

        return {"files": total_files, "chunks": total_chunks}

    def _chunk_file(
            self, file_path: str, text: str, repo: RepoSlug, branch: BranchName, commit: str,
    ) -> list[Chunk]:
        # Delegate to the domain ChunkerFactory (tree-sitter code chunking,
        # header-aware markdown chunking, sliding-window fallback) instead of
        # re-implementing chunking here.
        return self._chunker_factory.chunk(file_path, text, repo, branch, commit)


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
                continue
            if b"\x00" in data[:8192] or len(data) > _MAX_FILE_BYTES:
                continue
            try:
                text = data.decode("utf-8")
            except Exception:
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
