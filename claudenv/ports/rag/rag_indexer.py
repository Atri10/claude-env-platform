"""
claude-env :: Ports - RAG indexer interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.rag import Chunk
from claudenv.domain.value_objects import BranchName, RepoSlug


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
