"""
claude-env :: Domain - Value Objects - RepoSlug
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RepoSlug:
    """Repository slug (namespace-safe identifier)."""
    value: str

    @classmethod
    def generate(cls, name: str) -> RepoSlug:
        s = re.sub(r"[^a-z0-9._-]+", "-", name.strip().lower())
        s = re.sub(r"-{2,}", "-", s).strip("-.")
        return cls(s or "repo")

    @classmethod
    def from_string(cls, s: str) -> RepoSlug:
        return cls(s)

    def __str__(self) -> str:
        return self.value
