"""
claude-env :: Application - RAG Service
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claudenv.domain.rag import (
    BranchName, Chunk, ChunkId, ChunkType, IndexState, RetrievalMode,
    RetrievalQuery, RetrievalResult, RepoSlug, RAGConfig,
)
from claudenv.domain.value_objects import ContentHash, Tier
from claudenv.ports import (
    IEmbeddingProvider, IRagBookkeeping, IRagIndexer,
    IRagRetriever, IReranker,
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
            chunker: RAGConfig,
    ):
        self.repo = repo
        self.branch = branch
        self.store = store
        self.bookkeeping = bookkeeping
        self.embedder = embedder
        self.chunker = chunker

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

        rows = []
        for chunk, vector in zip(chunks, vectors):
            chunk.embedding = vector
            rows.append(chunk.to_metadata())

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

        for chunk, vector in zip(chunks, vectors):
            chunk.embedding = vector

        rows = [c.to_metadata() for c in chunks]
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

        state = IndexState.create(
            repo=repo, branch=branch,
            table_name=str(RepoSlug(f"{repo}_{branch}")),
            commit=commit,
            model=self.embedder.model_name if hasattr(self.embedder, 'model_name') else "unknown",
        )
        state.chunk_count = total_chunks
        self.bookkeeping.set_index_state(state)

        return {"files": total_files, "chunks": total_chunks}

    def _chunk_file(
            self, file_path: str, text: str, repo: RepoSlug, branch: BranchName, commit: str,
    ) -> list[Chunk]:
        from pathlib import Path
        ext = Path(file_path).suffix.lower()

        if ext in (".md", ".mdx", ".rst"):
            return self._chunk_markdown(file_path, text, repo, branch, commit)
        elif ext in (".py", ".js", ".ts", ".go", ".java", ".rs", ".cpp", ".c", ".h", ".cs", ".kt"):
            return self._chunk_code(file_path, text, ext, repo, branch, commit)
        else:
            return self._chunk_fallback(file_path, text, repo, branch, commit)

    def _chunk_markdown(
            self, file_path: str, text: str, repo: RepoSlug, branch: BranchName, commit: str,
    ) -> list[Chunk]:
        lines = text.splitlines()
        chunks = []
        current_header = "preamble"
        current_lines = []
        start_line = 0

        for i, line in enumerate(lines):
            if line.startswith("#"):
                if current_lines:
                    chunks.append(self._make_chunk(
                        repo, branch, commit, file_path, "markdown",
                        ChunkType.SECTION, current_header[:80],
                        start_line + 1, i, "\n".join(current_lines),
                    ))
                current_header = line.lstrip("# ").strip()[:80] or "section"
                current_lines = [line]
                start_line = i
            else:
                current_lines.append(line)

        if current_lines:
            chunks.append(self._make_chunk(
                repo, branch, commit, file_path, "markdown",
                ChunkType.SECTION, current_header[:80],
                start_line + 1, len(lines), "\n".join(current_lines),
            ))

        return chunks

    def _chunk_code(
            self, file_path: str, text: str, ext: str, repo: RepoSlug, branch: BranchName, commit: str,
    ) -> list[Chunk]:
        lines = text.splitlines()
        target = self.chunker.chunk_target_tokens // 4
        overlap = self.chunker.chunk_overlap_tokens // 4
        chunks = []

        for i in range(0, len(lines), target - overlap):
            chunk_text = "\n".join(lines[i:i + target])
            if not chunk_text.strip():
                continue
            chunks.append(self._make_chunk(
                repo, branch, commit, file_path, ext.lstrip("."),
                ChunkType.WINDOW, f"chunk_{len(chunks)}",
                i + 1, min(i + target, len(lines)), chunk_text,
            ))
        return chunks

    def _chunk_fallback(
            self, file_path: str, text: str, repo: RepoSlug, branch: BranchName, commit: str,
    ) -> list[Chunk]:
        return self._chunk_code(file_path, text, Path(file_path).suffix.lstrip("."), repo, branch, commit)

    def _make_chunk(
            self, repo: RepoSlug, branch: BranchName, commit: str, file_path: str,
            file_type: str, sym_type: ChunkType, sym_name: str,
            start: int, end: int, text: str,
    ) -> Chunk:
        return Chunk(
            chunk_id=ChunkId.from_parts(repo, file_path, start, end),
            repo=repo, branch=branch, commit_sha=commit,
            file_path=file_path, file_type=file_type,
            symbol_type=sym_type, symbol_name=sym_name,
            start_line=start, end_line=end, text=text,
            content_hash=ContentHash.compute(text), tier=Tier.INTERNAL,
        )


@dataclass
class RagService:
    """High-level RAG service combining indexing and retrieval."""

    indexer: IRagIndexer
    retriever: IRagRetriever
    embedder: IEmbeddingProvider
    reranker: IReranker

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
        return self.retriever.search(rq)

    def index_file(
            self, repo: RepoSlug, branch: BranchName, commit: str,
            file_path: str, text: str,
    ) -> int:
        return self.indexer.index_file(repo, branch, commit, file_path, text)

    def reindex(self, repo: RepoSlug, branch: BranchName, changed_files: list[str]) -> dict[str, Any]:
        total = 0
        for f in changed_files:
            # In production, read file and call index_file
            total += 1
        return {"files": total, "chunks": 0}
