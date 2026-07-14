"""
claude-env :: Domain - Value Objects - ContentHash
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentHash:
    """SHA-256 content hash."""
    value: str

    @classmethod
    def compute(cls, data: str | bytes) -> ContentHash:
        if isinstance(data, str):
            data = data.encode("utf-8")
        return cls(hashlib.sha256(data).hexdigest())

    @classmethod
    def from_string(cls, s: str) -> ContentHash:
        return cls(s)

    def __str__(self) -> str:
        return self.value
