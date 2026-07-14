"""
claude-env :: Domain - Value Objects - RegexRule
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RegexRule:
    """Compiled regex rule with reason."""
    pattern: re.Pattern
    reason: str

    @classmethod
    def create(cls, pattern: str, reason: str) -> RegexRule:
        return cls(re.compile(pattern), reason)

    def matches(self, path: str) -> bool:
        return self.pattern.search(path) is not None

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RegexRule):
            return NotImplemented
        return (self.pattern.pattern, self.reason) == (other.pattern.pattern, other.reason)

    def __hash__(self) -> int:
        return hash((self.pattern.pattern, self.reason))

    def __str__(self) -> str:
        return self.reason
