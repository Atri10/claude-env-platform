"""
claude-env :: Adapters - LanceDB Vector Store
"""
from __future__ import annotations

import os
from pathlib import Path

from claudenv.domain.rag import Chunk, RetrievalQuery, RetrievalResult, RetrievalMode, BranchName, RepoSlug
from claudenv.ports.rag import IRagIndexer, IRagRetriever


LANCE_PATH = os.environ.get("LANCEDB_PATH", str(Path.home() / ".claude-env/knowledge/lancedb"))


def table_name(repo: RepoSlug, branch: BranchName) -> str:
    safe = lambda s: str(s).replace("/", "-").replace(" ", "_")
    return f"{safe(repo)}__{safe(branch)}"


class LanceDbVectorStore:
    """LanceDB adapter for vector + FTS hybrid search."""

    def __init__(self, dim: int = 768, path: str = LANCE_PATH):
        try:
            import lancedb
            import pyarrow as pa
        except ImportError:
            raise RuntimeError("lancedb not installed: pip install lancedb pyarrow")

        Path(path).expanduser().mkdir(parents=True, exist_ok=True)
        self.db = lancedb.connect(str(Path(path).expanduser()))
        self.dim = dim

    def _schema(self):
        import pyarrow as pa
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

    def _open(self, repo: RepoSlug, branch: BranchName):
        name = table_name(repo, branch)
        if name in self.db.table_names():
            return self.db.open_table(name)
        tbl = self.db.create_table(name, schema=self._schema())
        try:
            tbl.create_fts_index("text", replace=True)
        except Exception:
            pass  # FTS is optional
        return tbl

    def upsert(self, repo: RepoSlug, branch: BranchName, rows: list[dict]) -> int:
        if not rows:
            return 0
        tbl = self._open(repo, branch)
        ids = [r["chunk_id"] for r in rows]
        # Delete existing
        id_list = ",".join(f"'{i}'" for i in ids)
        try:
            tbl.delete(f"chunk_id IN ({id_list})")
        except Exception:
            pass
        tbl.add(rows)
        return len(rows)

    def delete_file(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        tbl = self._open(repo, branch)
        safe = file_path.replace("'", "''")
        tbl.delete(f"file_path = '{safe}'")

    def search(self, repo: RepoSlug, branch: BranchName, query: RetrievalQuery) -> list[RetrievalResult]:
        tbl = self._open(repo, branch)
        vector = query.query_vector
        text = query.query

        try:
            if query.mode == RetrievalMode.HYBRID and vector is not None:
                res = (tbl.search(query_type="hybrid")
                          .vector(vector).text(text)
                          .limit(query.top_k).to_list())
            elif vector is not None:
                res = tbl.search(vector).limit(query.top_k).to_list()
            else:
                res = tbl.search(text, query_type="fts").limit(query.top_k).to_list()
        except Exception:
            if vector is not None:
                res = tbl.search(vector).limit(query.top_k).to_list()
            else:
                res = tbl.search(text, query_type="fts").limit(query.top_k).to_list()

        results = []
        for i, r in enumerate(res):
            r.pop("vector", None)
            chunk = Chunk.from_metadata(r)
            results.append(RetrievalResult(chunk=chunk, score=r.get("_score", 0.0), rank=i))

        return results

    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        try:
            return self._open(repo, branch).count_rows()
        except Exception:
            return 0