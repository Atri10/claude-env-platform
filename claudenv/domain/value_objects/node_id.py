"""
claude-env :: Domain - Value Objects - NodeId
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NodeId:
    """Memory node identifier."""
    value: str

    @classmethod
    def generate(cls) -> NodeId:
        return cls(f"mem-{uuid.uuid4().hex}")

    @classmethod
    def from_string(cls, s: str) -> NodeId:
        return cls(s)

    def __str__(self) -> str:
        return self.value
