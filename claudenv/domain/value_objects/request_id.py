"""
claude-env :: Domain - Value Objects - RequestId
"""
from __future__ import annotations

import secrets
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestId:
    """Approval request identifier."""
    value: str

    @classmethod
    def generate(cls) -> RequestId:
        return cls(f"appr-{secrets.token_urlsafe(12)}")

    @classmethod
    def from_string(cls, s: str) -> RequestId:
        return cls(s)

    def __str__(self) -> str:
        return self.value
