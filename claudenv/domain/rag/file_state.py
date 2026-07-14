"""
claude-env :: Domain - RAG Entities - FileState
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from claudenv.domain.value_objects import BranchName, ContentHash, RepoSlug, utc_now


@dataclass(frozen=True, slots=True)
class FileState:
    """Per-file indexing state."""
    repo: RepoSlug
    branch: BranchName
    file_path: str
    content_hash: ContentHash
    chunk_count: int
    indexed_at: datetime

    @classmethod
    def create(
            cls,
            repo: RepoSlug,
            branch: BranchName,
            file_path: str,
            content_hash: ContentHash,
            chunk_count: int,
    ) -> FileState:
        return cls(
            repo=repo,
            branch=branch,
            file_path=file_path,
            content_hash=content_hash,
            chunk_count=chunk_count,
            indexed_at=utc_now(),
        )
