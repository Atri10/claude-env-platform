"""
claude-env :: Domain - Policy Rule Strategies - SecretContentRule
"""
from __future__ import annotations

from claudenv.domain.policy import ContentPattern
from claudenv.domain.policy_rules.i_content_rule import IContentRule


class SecretContentRule(IContentRule):
    """Secret/PII content pattern matching."""

    def __init__(self, name: str, pattern: str):
        self._name = name
        self._rule = ContentPattern.create(name, pattern)

    def findall(self, text: str) -> list[str]:
        return self._rule.findall(text)

    def sub(self, text: str, replacement: str) -> str:
        return self._rule.sub(text, replacement)

    @property
    def name(self) -> str:
        return self._name

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, SecretContentRule):
            return NotImplemented
        return (self._name, self._rule.pattern.pattern) == (other._name, other._rule.pattern.pattern)

    def __hash__(self) -> int:
        return hash((self._name, self._rule.pattern.pattern))
