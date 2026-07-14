"""
claude-env :: Domain - Value Objects - EdgeId
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EdgeId:
    """Memory edge identifier."""
    value: str

    @classmethod
    def generate(cls) -> EdgeId:
        return cls(f"edge-{uuid.uuid4().hex}")

    @classmethod
    def from_string(cls, s: str) -> EdgeId:
        return cls(s)

    def __str__(self) -> str:
        return self.value
