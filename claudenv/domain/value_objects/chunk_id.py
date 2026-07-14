"""
claude-env :: Domain - Value Objects - ChunkId
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ChunkId:
    """RAG chunk identifier."""
    value: str

    @classmethod
    def from_parts(cls, repo: str, path: str, start: int, end: int) -> ChunkId:
        import hashlib
        raw = f"{repo}:{path}:{start}:{end}"
        return cls(hashlib.sha1(raw.encode()).hexdigest())

    @classmethod
    def from_string(cls, s: str) -> ChunkId:
        return cls(s)

    def __str__(self) -> str:
        return self.value
