"""
claude-env :: Adapters - LanceDB Vector Store
"""
from __future__ import annotations

from pathlib import Path

try:
    import lancedb
    import pyarrow as pa
except ImportError:
    lancedb = None
    pa = None

from claudenv.domain.rag import (
    BranchName, Chunk, RetrievalQuery, RetrievalResult, RepoSlug,
)


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

    def get_chunk(self, repo: RepoSlug, branch: BranchName, chunk_id: str) -> Chunk | None:
        # Avoid to_pandas()/to_lance() -- both require the optional `pylance`
        # package. A plain filtered scan (no query vector) doesn't.
        try:
            tbl = self.open(repo, branch)
            safe_id = chunk_id.replace("'", "''")
            rows = tbl.search().where(f"chunk_id = '{safe_id}'").limit(1).to_list()
        except Exception:
            return None
        if not rows:
            return None
        row = dict(rows[0])
        row.pop("vector", None)
        row.pop("_distance", None)
        return Chunk.from_metadata(row)



