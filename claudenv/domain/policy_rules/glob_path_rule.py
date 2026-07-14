"""
claude-env :: Domain - Policy Rule Strategies - GlobPathRule
"""
from __future__ import annotations

from claudenv.domain.policy import GlobPattern
from claudenv.domain.policy_rules.i_path_rule import IPathRule


class GlobPathRule(IPathRule):
    """Gitignore-style glob pattern matching."""

    def __init__(self, pattern: str):
        self._pattern = GlobPattern(pattern)

    def matches(self, path: str) -> bool:
        return self._pattern.matches(path)

    def describe(self) -> str:
        return f"glob:{self._pattern.pattern}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, GlobPathRule):
            return NotImplemented
        return self._pattern.pattern == other._pattern.pattern

    def __hash__(self) -> int:
        return hash(self._pattern.pattern)
