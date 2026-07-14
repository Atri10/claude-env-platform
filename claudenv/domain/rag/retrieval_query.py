"""
claude-env :: Domain - RAG Entities - RetrievalQuery
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import BranchName, RepoSlug, Tier
from claudenv.domain.rag.retrieval_mode import RetrievalMode


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
