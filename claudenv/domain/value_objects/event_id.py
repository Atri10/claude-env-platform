"""
claude-env :: Domain - Value Objects - EventId
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EventId:
    """Audit event identifier."""
    value: str

    @classmethod
    def generate(cls) -> EventId:
        return cls(f"evt-{uuid.uuid4().hex[:12]}")

    @classmethod
    def from_string(cls, s: str) -> EventId:
        return cls(s)

    def __str__(self) -> str:
        return self.value
