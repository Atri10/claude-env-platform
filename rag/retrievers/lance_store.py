"""
claude-env :: LanceDB store
File: rag/retrievers/lance_store.py
Purpose:
    Thin wrapper over LanceDB giving per-(repo, branch) tables, vector + FTS
    hybrid search, and upsert/delete keyed by chunk_id for incremental indexing.

LanceDB layout (on disk, fully local):
    ~/.claude-env/knowledge/lancedb/
        <repo-slug>__<branch>.lance/      <- one table per repo+branch
    Each row: chunk_id, vector(768), text, + all chunk metadata columns.

Hybrid search:
    dense vector search (cosine) fused with full-text (BM25-style) search via
    reciprocal-rank fusion. LanceDB's native hybrid query is used when the FTS
    index exists; otherwise we fall back to vector-only.
"""
from __future__ import annotations

import os
from pathlib import Path

try:
    import lancedb
    import pyarrow as pa
except ImportError:  # pragma: no cover
    lancedb = None
    pa = None

LANCE_PATH = os.environ.get(
    "LANCEDB_PATH", str(Path.home() / ".claude-env/knowledge/lancedb"))


def table_name(repo: str, branch: str) -> str:
    safe = lambda s: s.replace("/", "-").replace(" ", "_")
    return f"{safe(repo)}__{safe(branch)}"


class LanceStore:
    def __init__(self, dim: int = 768, path: str = LANCE_PATH):
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

    def open(self, repo: str, branch: str):
        name = table_name(repo, branch)
        if name in self.db.table_names():
            return self.db.open_table(name)
        tbl = self.db.create_table(name, schema=self._schema())
        try:
            tbl.create_fts_index("text", replace=True)
        except Exception:
            pass
        return tbl

    def upsert(self, repo: str, branch: str, rows: list[dict]) -> int:
        if not rows:
            return 0
        tbl = self.open(repo, branch)
        ids = [r["chunk_id"] for r in rows]
        # delete-then-add = idempotent upsert keyed by chunk_id
        id_list = ",".join(f"'{i}'" for i in ids)
        try:
            tbl.delete(f"chunk_id IN ({id_list})")
        except Exception:
            pass
        tbl.add(rows)
        return len(rows)

    def delete_file(self, repo: str, branch: str, file_path: str) -> None:
        tbl = self.open(repo, branch)
        # escape single quotes so paths like docs/what's-new.md don't break the
        # LanceDB SQL filter (and crash indexing mid-run).
        safe = file_path.replace("'", "''")
        tbl.delete(f"file_path = '{safe}'")

    def search(self, repo: str, branch: str, query_vector: list[float],
               query_text: str, top_k: int = 40) -> list[dict]:
        tbl = self.open(repo, branch)
        try:
            res = (tbl.search(query_type="hybrid")
                      .vector(query_vector).text(query_text)
                      .limit(top_k).to_list())
        except Exception:
            res = tbl.search(query_vector).limit(top_k).to_list()
        for r in res:
            r.pop("vector", None)
        return res

    def count(self, repo: str, branch: str) -> int:
        try:
            return self.open(repo, branch).count_rows()
        except Exception:
            return 0
