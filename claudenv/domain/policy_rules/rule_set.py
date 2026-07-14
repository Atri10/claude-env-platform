"""
claude-env :: Domain - Policy Rule Strategies - RuleSet
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import Action
from claudenv.domain.policy_rules.i_path_rule import IPathRule
from claudenv.domain.policy_rules.i_extension_rule import IExtensionRule


@dataclass
class RuleSet:
    """Collection of rules for a policy scope (global/repo)."""
    deny_paths: list[IPathRule] = None
    deny_extensions: list[IExtensionRule] = None
    deny_regex: list[IPathRule] = None
    allow_paths: list[IPathRule] = None
    allow_extensions: list[IExtensionRule] = None
    override_paths: list[IPathRule] = None
    default_deny: bool = False

    def __post_init__(self):
        self.deny_paths = self.deny_paths or []
        self.deny_extensions = self.deny_extensions or []
        self.deny_regex = self.deny_regex or []
        self.allow_paths = self.allow_paths or []
        self.allow_extensions = self.allow_extensions or []
        self.override_paths = self.override_paths or []

    def check_deny(self, path: str) -> tuple[Action, str, str] | None:
        """Check if path is denied. Returns (action, reason, rule) or None."""
        for rule in self.deny_paths:
            if rule.matches(path):
                return Action.BLOCK, f"deny path: {rule.describe()}", rule.describe()
        for rule in self.deny_extensions:
            if rule.matches(path):
                return Action.BLOCK, f"deny extension: {rule.extension}", rule.extension
        for rule in self.deny_regex:
            if rule.matches(path):
                return Action.BLOCK, f"deny regex: {rule.describe()}", rule.describe()
        return None

    def check_allow(self, path: str) -> bool:
        """Check if path is explicitly allowed."""
        for rule in self.allow_paths:
            if rule.matches(path):
                return True
        for rule in self.allow_extensions:
            if rule.matches(path):
                return True
        return False

    def check_override(self, path: str) -> bool:
        """Check if path matches override_deny (repo escape hatch)."""
        for rule in self.override_paths:
            if rule.matches(path):
                return True
        return False
