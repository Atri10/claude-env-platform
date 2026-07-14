"""
claude-env :: Domain - Value Objects - TableName
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects.repo_slug import RepoSlug
from claudenv.domain.value_objects.branch_name import BranchName


@dataclass(frozen=True, slots=True)
class TableName:
    """LanceDB table name (repo__branch)."""
    repo: RepoSlug
    branch: BranchName

    @classmethod
    def generate(cls, repo: RepoSlug, branch: BranchName) -> TableName:
        safe = lambda s: s.replace("/", "-").replace(" ", "_")
        return cls(RepoSlug(f"{safe(str(repo))}__{safe(str(branch))}"), BranchName(""))

    def __str__(self) -> str:
        return str(self.repo)
