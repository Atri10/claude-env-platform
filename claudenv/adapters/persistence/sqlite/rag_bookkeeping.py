"""
claude-env :: Adapters - SQLite Persistence - RAG Bookkeeping

SQLite implementation of ``IRagBookkeeping``. Tracks per-file content hashes
(for incremental indexing) and per-repo/branch index state.
"""
from __future__ import annotations

from datetime import datetime

from claudenv.domain.rag import BranchName, IndexState, RepoSlug
from claudenv.domain.value_objects import ContentHash, utc_now
from claudenv.ports import IRagBookkeeping

from .database import SQLiteDatabase


class SQLiteRagBookkeeping(IRagBookkeeping):
    """SQLite RAG file state and index state."""

    def __init__(self, db: SQLiteDatabase):
        self._db = db

    def get_file_hash(self, repo: RepoSlug, branch: BranchName, file_path: str) -> ContentHash | None:
        row = self._db.query_one(
            "SELECT content_hash FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
            (str(repo), str(branch), file_path),
        )
        return ContentHash.from_string(row["content_hash"]) if row else None

    def set_file_hash(
            self, repo: RepoSlug, branch: BranchName, file_path: str,
            content_hash: ContentHash, chunk_count: int,
    ) -> None:
        self._db.execute(
            "INSERT INTO rag_file_state (repo, branch, file_path, content_hash, chunk_count, indexed_at) "
            "VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(repo, branch, file_path) DO UPDATE SET "
            "content_hash=excluded.content_hash, chunk_count=excluded.chunk_count, "
            "indexed_at=excluded.indexed_at",
            (str(repo), str(branch), file_path, str(content_hash), chunk_count, utc_now().isoformat()),
        )

    def delete_file_hash(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        self._db.execute(
            "DELETE FROM rag_file_state WHERE repo=? AND branch=? AND file_path=?",
            (str(repo), str(branch), file_path),
        )

    def get_index_state(self, repo: RepoSlug, branch: BranchName) -> IndexState | None:
        row = self._db.query_one(
            "SELECT * FROM rag_index_state WHERE repo=? AND branch=?",
            (str(repo), str(branch)),
        )
        if not row:
            return None
        return IndexState(
            repo=RepoSlug.from_string(row["repo"]),
            branch=BranchName.from_string(row["branch"]),
            table_name=row["table_name"],
            last_commit=row["last_commit"],
            chunk_count=row["chunk_count"],
            embed_model=row["embed_model"],
            updated_at=datetime.fromisoformat(row["updated_at"].replace("Z", "+00:00")),
        )

    def set_index_state(self, state: IndexState) -> None:
        self._db.execute(
            "INSERT INTO rag_index_state (repo, branch, table_name, last_commit, chunk_count, embed_model, updated_at) "
            "VALUES (?,?,?,?,?,?,?) "
            "ON CONFLICT(repo, branch) DO UPDATE SET "
            "last_commit=excluded.last_commit, chunk_count=excluded.chunk_count, "
            "updated_at=excluded.updated_at, embed_model=excluded.embed_model",
            (str(state.repo), str(state.branch), state.table_name, state.last_commit,
             state.chunk_count, state.embed_model, state.updated_at.isoformat()),
        )
