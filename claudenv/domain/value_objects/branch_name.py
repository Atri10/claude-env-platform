"""
claude-env :: Domain - Value Objects - BranchName
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class BranchName:
    """Git branch name."""
    value: str

    @classmethod
    def from_string(cls, s: str) -> BranchName:
        return cls(s)

    @classmethod
    def default(cls) -> BranchName:
        return cls("main")

    def __str__(self) -> str:
        return self.value
