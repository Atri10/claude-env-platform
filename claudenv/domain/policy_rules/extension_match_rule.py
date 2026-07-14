"""
claude-env :: Domain - Policy Rule Strategies - ExtensionMatchRule
"""
from __future__ import annotations

from claudenv.domain.policy import ExtensionRule
from claudenv.domain.policy_rules.i_extension_rule import IExtensionRule


class ExtensionMatchRule(IExtensionRule):
    """File extension matching with dotfile variant support."""

    def __init__(self, extension: str):
        self._extension = extension
        self._rule = ExtensionRule(extension)

    def matches(self, path: str) -> bool:
        return self._rule.matches(path)

    @property
    def extension(self) -> str:
        return self._extension

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ExtensionMatchRule):
            return NotImplemented
        return self._extension == other._extension

    def __hash__(self) -> int:
        return hash(self._extension)
