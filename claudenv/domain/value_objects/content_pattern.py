"""
claude-env :: Domain - Value Objects - ContentPattern
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentPattern:
    """Content scanning pattern (for secrets/PII)."""
    name: str
    pattern: re.Pattern

    @classmethod
    def create(cls, name: str, pattern: str) -> ContentPattern:
        return cls(name, re.compile(pattern))

    def findall(self, text: str) -> list[str]:
        return self.pattern.findall(text)

    def sub(self, text: str, replacement: str) -> str:
        return self.pattern.sub(replacement, text)
