"""
claude-env :: Domain - RAG Entities - Models

Groups: ChunkType, RetrievalMode, Chunk, FileState, IndexState,
RetrievalQuery, RetrievalResult.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from claudenv.domain.value_objects import (
    BranchName,
    ChunkId,
    ContentHash,
    RepoSlug,
    Tier,
    utc_now,
)


class ChunkType(str, Enum):
    """Types of code chunks."""
    FUNCTION = "function"
    CLASS = "class"
    SECTION = "section"
    WINDOW = "window"


class RetrievalMode(str, Enum):
    """Retrieval modes."""
    VECTOR = "vector"
    FTS = "fts"
    HYBRID = "hybrid"


@dataclass(frozen=True, slots=True)
class Chunk:
    """A chunk of source code/text with metadata."""
    chunk_id: ChunkId
    repo: RepoSlug
    branch: BranchName
    commit_sha: str
    file_path: str
    file_type: str
    symbol_type: ChunkType
    symbol_name: str
    start_line: int
    end_line: int
    text: str
    content_hash: ContentHash
    tier: Tier
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_metadata(self) -> dict[str, Any]:
        """Convert to LanceDB metadata format."""
        return {
            "chunk_id": str(self.chunk_id),
            "repo": str(self.repo),
            "branch": str(self.branch),
            "commit_sha": self.commit_sha,
            "file_path": self.file_path,
            "file_type": self.file_type,
            "symbol_type": self.symbol_type.value,
            "symbol_name": self.symbol_name,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "text": self.text,
            "content_hash": str(self.content_hash),
            "tier": int(self.tier),
            **self.metadata,
        }

    @classmethod
    def from_metadata(cls, meta: dict[str, Any]) -> Chunk:
        return cls(
            chunk_id=ChunkId.from_string(meta["chunk_id"]),
            repo=RepoSlug.from_string(meta["repo"]),
            branch=BranchName.from_string(meta["branch"]),
            commit_sha=meta["commit_sha"],
            file_path=meta["file_path"],
            file_type=meta["file_type"],
            symbol_type=ChunkType(meta["symbol_type"]),
            symbol_name=meta["symbol_name"],
            start_line=meta["start_line"],
            end_line=meta["end_line"],
            text=meta["text"],
            content_hash=ContentHash.from_string(meta["content_hash"]),
            tier=Tier(meta["tier"]),
            metadata={k: v for k, v in meta.items() if k not in (
                "chunk_id", "repo", "branch", "commit_sha", "file_path",
                "file_type", "symbol_type", "symbol_name", "start_line",
                "end_line", "text", "content_hash", "tier",
            )},
        )


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


@dataclass(frozen=True, slots=True)
class IndexState:
    """State of the RAG index for a repo+branch."""
    repo: RepoSlug
    branch: BranchName
    table_name: str
    last_commit: str
    chunk_count: int
    embed_model: str
    updated_at: datetime

    @classmethod
    def create(
            cls,
            repo: RepoSlug,
            branch: BranchName,
            table_name: str,
            commit: str,
            model: str,
    ) -> IndexState:
        return cls(
            repo=repo,
            branch=branch,
            table_name=table_name,
            last_commit=commit,
            chunk_count=0,
            embed_model=model,
            updated_at=utc_now(),
        )


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    """A retrieval query with options."""
    query: str
    query_vector: list[float] | None
    top_k: int
    mode: RetrievalMode
    repo_filter: RepoSlug | None
    branch_filter: BranchName | None
    tier_filter: Tier | None


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    """A ranked retrieval result."""
    chunk: Chunk
    score: float
    rank: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk": self.chunk.to_metadata(),
            "score": self.score,
            "rank": self.rank,
        }
