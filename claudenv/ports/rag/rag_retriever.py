"""
claude-env :: Ports - RAG retriever interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.rag import RetrievalQuery, RetrievalResult
from claudenv.domain.value_objects import BranchName, RepoSlug


class IRagRetriever(Protocol):
    """Retrieval operations for RAG."""

    @abstractmethod
    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        ...

    @abstractmethod
    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        ...
