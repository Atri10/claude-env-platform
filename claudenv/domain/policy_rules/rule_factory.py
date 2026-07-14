"""
claude-env :: Domain - Policy Rule Strategies - RuleFactory
"""
from __future__ import annotations

from claudenv.domain.policy_rules.i_path_rule import IPathRule
from claudenv.domain.policy_rules.i_extension_rule import IExtensionRule
from claudenv.domain.policy_rules.i_content_rule import IContentRule
from claudenv.domain.policy_rules.glob_path_rule import GlobPathRule
from claudenv.domain.policy_rules.regex_path_rule import RegexPathRule
from claudenv.domain.policy_rules.extension_match_rule import ExtensionMatchRule
from claudenv.domain.policy_rules.secret_content_rule import SecretContentRule


class RuleFactory:
    """Factory for creating rule instances from configuration."""

    @staticmethod
    def create_path_rule(pattern: str) -> IPathRule:
        # Check if it's a glob pattern (contains *, ?, **)
        if any(c in pattern for c in "*?["):
            return GlobPathRule(pattern)
        # Otherwise treat as regex
        return RegexPathRule(pattern)

    @staticmethod
    def create_glob_rule(pattern: str) -> IPathRule:
        return GlobPathRule(pattern)

    @staticmethod
    def create_regex_rule(pattern: str, reason: str = "regex") -> IPathRule:
        return RegexPathRule(pattern, reason)

    @staticmethod
    def create_extension_rule(extension: str) -> IExtensionRule:
        return ExtensionMatchRule(extension)

    @staticmethod
    def create_content_rule(name: str, pattern: str) -> IContentRule:
        return SecretContentRule(name, pattern)
