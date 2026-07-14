"""
claude-env :: Domain - RAG Entities - Chunk
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from claudenv.domain.value_objects import BranchName, ChunkId, ContentHash, RepoSlug, Tier
from claudenv.domain.rag.chunk_type import ChunkType


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
