"""
claude-env :: Ports - RAG bookkeeping interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.rag import IndexState
from claudenv.domain.value_objects import BranchName, ContentHash, RepoSlug


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
