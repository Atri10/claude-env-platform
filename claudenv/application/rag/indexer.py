"""
claude-env :: Application - RAG Service - RagIndexer
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from claudenv.domain.rag import (
    BranchName,
    Chunk,
    IndexState,
    RAGConfig,
    RepoSlug,
)
from claudenv.domain.rag_chunker import ChunkerFactory, ChunkingConfig, make_chunker_factory
from claudenv.domain.value_objects import ContentHash
from claudenv.ports import (
    IEmbeddingProvider,
    IRagBookkeeping,
    IRagIndexer,
    IRagRetriever,
)


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
