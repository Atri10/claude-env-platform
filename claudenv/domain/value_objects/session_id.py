"""
claude-env :: Domain - Value Objects - SessionId
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SessionId:
    """Session identifier."""
    value: str

    @classmethod
    def generate(cls) -> SessionId:
        return cls(f"sess-{uuid.uuid4().hex[:12]}")

    @classmethod
    def from_string(cls, s: str) -> SessionId:
        return cls(s)

    def __str__(self) -> str:
        return self.value
