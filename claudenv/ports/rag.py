"""
claude-env :: Ports - RAG Interfaces
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.rag import (
    Chunk, IndexState, RetrievalMode,
    RetrievalQuery, RetrievalResult,
)
from claudenv.domain.value_objects import (
    BranchName, ContentHash, RepoSlug, Tier,
)


class IRagIndexer(Protocol):
    """Indexing operations for RAG."""

    @abstractmethod
    def index_file(
            self,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            file_path: str,
            text: str,
    ) -> int:
        ...

    @abstractmethod
    def index_batch(
            self,
            repo: RepoSlug,
            branch: BranchName,
            commit: str,
            chunks: list[Chunk],
    ) -> int:
        ...

    @abstractmethod
    def delete_file(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        ...

    @abstractmethod
    def full_index(
            self,
            repo: RepoSlug,
            branch: BranchName,
            files: dict[str, str],
    ) -> dict[str, Any]:
        ...


class IRagRetriever(Protocol):
    """Retrieval operations for RAG."""

    @abstractmethod
    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        ...

    @abstractmethod
    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        ...


class IRagBookkeeping(Protocol):
    """RAG index bookkeeping (file hashes, index state)."""

    @abstractmethod
    def get_file_hash(self, repo: RepoSlug, branch: BranchName, file_path: str) -> ContentHash | None:
        ...

    @abstractmethod
    def set_file_hash(
            self,
            repo: RepoSlug,
            branch: BranchName,
            file_path: str,
            content_hash: ContentHash,
            chunk_count: int,
    ) -> None:
        ...

    @abstractmethod
    def delete_file_hash(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        ...

    @abstractmethod
    def get_index_state(self, repo: RepoSlug, branch: BranchName) -> IndexState | None:
        ...

    @abstractmethod
    def set_index_state(self, state: IndexState) -> None:
        ...


class IReranker(Protocol):
    """Reranker for improving result ordering."""

    @abstractmethod
    def rerank(self, query: str, docs: list[str], top_k: int) -> list[int]:
        ...

    def status(self) -> dict[str, Any]:
        return {"ok": True, "backend": self.__class__.__name__}
