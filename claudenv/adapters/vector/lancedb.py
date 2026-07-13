"""
claude-env :: Adapters - LanceDB Vector Store
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

try:
    import lancedb
    import pyarrow as pa
except ImportError:
    lancedb = None
    pa = None

from claudenv.domain.rag import (
    BranchName, Chunk, ChunkId, ContentHash, IndexState, RAGConfig,
    RetrievalQuery, RetrievalResult, RepoSlug, TableName,
)
from claudenv.ports import IRagIndexer, IRagRetriever


class LanceDbVectorStore:
    """LanceDB vector store adapter."""

    def __init__(self, path: str, dim: int = 768):
        if lancedb is None:
            raise RuntimeError("lancedb not installed: pip install lancedb pyarrow")
        Path(path).expanduser().mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(Path(path).expanduser()))
        self.dim = dim

    def _schema(self) -> "pa.Schema":
        return pa.schema([
            pa.field("chunk_id", pa.string()),
            pa.field("vector", pa.list_(pa.float32(), self.dim)),
            pa.field("text", pa.string()),
            pa.field("repo", pa.string()),
            pa.field("branch", pa.string()),
            pa.field("commit_sha", pa.string()),
            pa.field("file_path", pa.string()),
            pa.field("file_type", pa.string()),
            pa.field("symbol_type", pa.string()),
            pa.field("symbol_name", pa.string()),
            pa.field("start_line", pa.int32()),
            pa.field("end_line", pa.int32()),
            pa.field("content_hash", pa.string()),
            pa.field("tier", pa.int32()),
        ])

    def _table_name(self, repo: RepoSlug, branch: BranchName) -> str:
        safe = lambda s: s.replace("/", "-").replace(" ", "_")
        return f"{safe(str(repo))}__{safe(str(branch))}"

    def open(self, repo: RepoSlug, branch: BranchName):
        name = self._table_name(repo, branch)
        if name in self.db.table_names():
            return self.db.open_table(name)
        tbl = self.db.create_table(name, schema=self._schema())
        try:
            tbl.create_fts_index("text", replace=True)
        except Exception:
            pass  # FTS is optional optimization
        return tbl

    def upsert(self, repo: RepoSlug, branch: BranchName, rows: list[dict]) -> int:
        if not rows:
            return 0
        tbl = self.open(repo, branch)
        ids = [r["chunk_id"] for r in rows]
        id_list = ",".join(f"'{i}'" for i in ids)
        try:
            tbl.delete(f"chunk_id IN ({id_list})")
        except Exception:
            pass
        tbl.add(rows)
        return len(rows)

    def delete_file(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        tbl = self.open(repo, branch)
        safe = file_path.replace("'", "''")
        tbl.delete(f"file_path = '{safe}'")

    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        tbl = self.open(query.repo_filter or RepoSlug("default"), query.branch_filter or BranchName("main"))
        top_k = query.top_k

        try:
            if query.mode == "hybrid" and query.query_vector and query.query:
                res = (tbl.search(query_type="hybrid")
                       .vector(query.query_vector).text(query.query)
                       .limit(top_k).to_list())
            elif query.query_vector:
                res = tbl.search(query.query_vector).limit(top_k).to_list()
            else:
                res = tbl.search(query.query).limit(top_k).to_list()
        except Exception:
            res = tbl.search(query.query_vector or [0.0] * self.dim).limit(top_k).to_list()

        results = []
        for i, r in enumerate(res):
            r.pop("vector", None)
            chunk = Chunk.from_metadata(r)
            results.append(RetrievalResult(chunk=chunk, score=r.get("_score", 0.0), rank=i + 1))
        return results

    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        try:
            return self.open(repo, branch).count_rows()
        except Exception:
            return 0


class LanceDbRagIndexer(IRagIndexer):
    """RAG indexer using LanceDB."""

    def __init__(
            self,
            repo: RepoSlug,
            branch: BranchName,
            store: LanceDbVectorStore,
            bookkeeping: Any,  # IRagBookkeeping
            embedder: Any,  # IEmbeddingProvider
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
        # Chunk the file
        chunks = self._chunk_file(file_path, text, repo, branch, commit)
        if not chunks:
            return 0

        # Embed chunks
        texts = [c.text for c in chunks]
        vectors = self.embedder.embed_documents(texts)

        # Build rows
        rows = []
        for chunk, vec in zip(chunks, vectors):
            rows.append({
                "chunk_id": str(chunk.chunk_id),
                "vector": vec,
                "text": chunk.text,
                "repo": str(chunk.repo),
                "branch": str(chunk.branch),
                "commit_sha": chunk.commit_sha,
                "file_path": chunk.file_path,
                "file_type": chunk.file_type,
                "symbol_type": chunk.symbol_type.value,
                "symbol_name": chunk.symbol_name,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content_hash": str(chunk.content_hash),
                "tier": int(chunk.tier),
            })

        # Upsert
        self.store.upsert(repo, branch, rows)

        # Update bookkeeping
        content_hash = ContentHash.compute(text)
        self.bookkeeping.set_file_hash(repo, branch, file_path, content_hash, len(chunks))

        return len(chunks)

    def index_batch(
            self, repo: RepoSlug, branch: BranchName, commit: str, chunks: list[Chunk],
    ) -> int:
        texts = [c.text for c in chunks]
        vectors = self.embedder.embed_documents(texts)

        rows = []
        for chunk, vec in zip(chunks, vectors):
            rows.append({
                "chunk_id": str(chunk.chunk_id),
                "vector": vec,
                "text": chunk.text,
                "repo": str(chunk.repo),
                "branch": str(chunk.branch),
                "commit_sha": chunk.commit_sha,
                "file_path": chunk.file_path,
                "file_type": chunk.file_type,
                "symbol_type": chunk.symbol_type.value,
                "symbol_name": chunk.symbol_name,
                "start_line": chunk.start_line,
                "end_line": chunk.end_line,
                "content_hash": str(chunk.content_hash),
                "tier": int(chunk.tier),
            })

        self.store.upsert(repo, branch, rows)
        return len(rows)

    def delete_file(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        self.store.delete_file(repo, branch, file_path)
        self.bookkeeping.delete_file_hash(repo, branch, file_path)

    def full_index(
            self, repo: RepoSlug, branch: BranchName, files: dict[str, str],
    ) -> dict[str, Any]:
        total_chunks = total_files = 0
        commit = "0"  # placeholder

        for file_path, text in files.items():
            n = self.index_file(repo, branch, commit, file_path, text)
            if n:
                total_files += 1
                total_chunks += n

        # Record index state
        state = IndexState.create(repo, branch, self.store._table_name(repo, branch), commit, "model")
        state.chunk_count = total_chunks
        self.bookkeeping.set_index_state(state)

        return {"files": total_files, "chunks": total_chunks}

    def _chunk_file(
            self, file_path: str, text: str, repo: RepoSlug, branch: BranchName, commit: str,
    ) -> list[Chunk]:
        # Simple chunking - in production use the proper chunker
        ext = Path(file_path).suffix.lower()
        target = self.chunker.chunk_target_tokens
        overlap = self.chunker.chunk_overlap_tokens

        # Rough token estimation
        tokens_per_char = 0.25
        chunk_chars = int(target / tokens_per_char)
        overlap_chars = int(overlap / tokens_per_char)

        chunks = []
        for i in range(0, len(text), chunk_chars - overlap_chars):
            chunk_text = text[i:i + chunk_chars]
            if not chunk_text.strip():
                continue
            chunk = Chunk(
                chunk_id=ChunkId.from_parts(repo, file_path, i // (chunk_chars - overlap_chars),
                                            i // (chunk_chars - overlap_chars) + 1),
                repo=repo,
                branch=branch,
                commit_sha=commit,
                file_path=file_path,
                file_type=ext.lstrip("."),
                symbol_type="window",
                symbol_name=f"chunk_{len(chunks)}",
                start_line=text[:i].count("\n") + 1,
                end_line=text[:i + len(chunk_text)].count("\n"),
                text=chunk_text,
                content_hash=ContentHash.compute(chunk_text),
                tier=self.chunker.tier if hasattr(self.chunker, "tier") else 1,
            )
            chunks.append(chunk)
        return chunks


class LanceDbRagRetriever(IRagRetriever):
    """RAG retriever using LanceDB."""

    def __init__(self, store: LanceDbVectorStore, embedder: Any, reranker: Any):
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        # Embed query if vector not provided
        if query.query_vector is None and query.query:
            query.query_vector = self.embedder.embed_query(query.query)

        results = self.store.search(query)

        # Rerank if available
        if self.reranker and query.query:
            texts = [r.chunk.text for r in results]
            reranked = self.reranker.rerank(query.query, texts, query.top_k)
            results = [results[i] for i in reranked]

        return results

    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        return self.store.count(repo, branch)
