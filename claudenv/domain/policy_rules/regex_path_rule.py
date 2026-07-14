"""
claude-env :: Domain - Policy Rule Strategies - RegexPathRule
"""
from __future__ import annotations

from claudenv.domain.policy import RegexRule
from claudenv.domain.policy_rules.i_path_rule import IPathRule


class RegexPathRule(IPathRule):
    """Regex pattern matching."""

    def __init__(self, pattern: str, reason: str = "regex"):
        self._rule = RegexRule.create(pattern, reason)

    def matches(self, path: str) -> bool:
        return self._rule.matches(path)

    def describe(self) -> str:
        return f"regex:{self._rule.reason}"

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, RegexPathRule):
            return NotImplemented
        return (self._rule.pattern.pattern, self._rule.reason) == (other._rule.pattern.pattern, other._rule.reason)

    def __hash__(self) -> int:
        return hash((self._rule.pattern.pattern, self._rule.reason))
