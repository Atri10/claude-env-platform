"""
claude-env :: Ports - Vector store interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.rag import Chunk, RetrievalQuery, RetrievalResult
from claudenv.domain.value_objects import BranchName, RepoSlug


class IVectorStore(Protocol):
    """Vector store abstraction (backed by LanceDB in production)."""

    @abstractmethod
    def open(self, repo: RepoSlug, branch: BranchName) -> Any:
        ...

    @abstractmethod
    def upsert(
            self, repo: RepoSlug, branch: BranchName, rows: list[dict[str, Any]]
    ) -> int:
        ...

    @abstractmethod
    def delete_file(self, repo: RepoSlug, branch: BranchName, file_path: str) -> None:
        ...

    @abstractmethod
    def search(self, query: RetrievalQuery) -> list[RetrievalResult]:
        ...

    @abstractmethod
    def count(self, repo: RepoSlug, branch: BranchName) -> int:
        ...

    @abstractmethod
    def get_chunk(
            self, repo: RepoSlug, branch: BranchName, chunk_id: str
    ) -> Chunk | None:
        ...
