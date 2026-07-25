"""
claude-env :: Adapters - LanceDB Vector Store
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

try:
    import lancedb
    import pyarrow as pa
except ImportError:
    logger.warning("lancedb/pyarrow not installed; vector features disabled", exc_info=True)
    lancedb = None
    pa = None

from claudenv.domain.rag import (  # noqa: E402
    BranchName,
    Chunk,
    RepoSlug,
    RetrievalQuery,
    RetrievalResult,
)


class LanceDbVectorStore:
    """LanceDB vector store adapter."""

    # The fixed pyarrow schema's core columns. Chunk.to_metadata() also
    # includes an arbitrary `**self.metadata` dict (chunker name, markdown
    # header, tree-sitter node type, ...) which has no column of its own --
    # it's folded into the `metadata` JSON string column instead so
    # `tbl.add()` doesn't reject unknown fields.
    _CORE_FIELDS = frozenset({
        "chunk_id", "vector", "text", "repo", "branch", "commit_sha",
        "file_path", "file_type", "symbol_type", "symbol_name",
        "start_line", "end_line", "content_hash", "tier",
    })

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
            pa.field("metadata", pa.string()),
        ])

    def _to_row(self, row: dict) -> dict:
        """Fold any key outside the fixed schema into a `metadata` JSON blob."""
        core = {k: row[k] for k in self._CORE_FIELDS if k in row}
        extra = {k: v for k, v in row.items() if k not in self._CORE_FIELDS}
        core["metadata"] = json.dumps(extra)
        return core

    @staticmethod
    def _expand_metadata(row: dict) -> dict:
        """Inverse of `_to_row`'s metadata folding, applied to a search-result row."""
        blob = row.pop("metadata", None)
        if blob:
            try:
                row.update(json.loads(blob))
            except (TypeError, ValueError):
                logger.warning("failed to parse stored JSON blob; skipping field", exc_info=True)
                pass
        return row

    def _table_name(self, repo: RepoSlug, branch: BranchName) -> str:
        safe = lambda s: s.replace("/", "-").replace(" ", "_")
        return f"{safe(str(repo))}__{safe(str(branch))}"

    def open(self, repo: RepoSlug, branch: BranchName):
        name = self._table_name(repo, branch)
        if name in self.db.list_tables():
            return self.db.open_table(name)
        try:
            tbl = self.db.create_table(name, schema=self._schema())
        except ValueError:
            # Race: another connection created the table between list_tables
            # and create_table. Use the existing one.
            return self.db.open_table(name)
        try:
            tbl.create_fts_index("text", replace=True)
        except Exception:
            logger.warning("FTS index creation failed; search quality degraded", exc_info=True)
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
            logger.warning("chunk delete failed; will re-add", exc_info=True)
            pass
        tbl.add([self._to_row(r) for r in rows])
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
            logger.warning("vector search failed; falling back to text search", exc_info=True)
            res = tbl.search(query.query_vector or [0.0] * self.dim).limit(top_k).to_list()

        results = []
        for i, r in enumerate(res):
            r.pop("vector", None)
            r = self._expand_metadata(r)
            chunk = Chunk.from_metadata(r)
            results.append(RetrievalResult(chunk=chunk, score=r.get("_score", 0.0), rank=i + 1))
        return results

    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        try:
            return self.open(repo, branch).count_rows()
        except Exception:
            logger.warning("count_rows failed; returning 0", exc_info=True)
            return 0

    def get_chunk(self, repo: RepoSlug, branch: BranchName, chunk_id: str) -> Chunk | None:
        # Avoid to_pandas()/to_lance() -- both require the optional `pylance`
        # package. A plain filtered scan (no query vector) doesn't.
        try:
            tbl = self.open(repo, branch)
            safe_id = chunk_id.replace("'", "''")
            rows = tbl.search().where(f"chunk_id = '{safe_id}'").limit(1).to_list()
        except Exception:
            logger.warning("get_chunk failed; returning None", exc_info=True)
            return None
        if not rows:
            return None
        row = dict(rows[0])
        row.pop("vector", None)
        row.pop("_distance", None)
        return Chunk.from_metadata(self._expand_metadata(row))
