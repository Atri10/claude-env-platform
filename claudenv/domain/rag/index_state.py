"""
claude-env :: Domain - RAG Entities - IndexState
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from claudenv.domain.value_objects import BranchName, RepoSlug, utc_now


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
