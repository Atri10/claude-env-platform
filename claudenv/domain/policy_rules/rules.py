"""
claude-env :: Domain - Policy Rule Strategies - Rules

Groups: GlobPathRule, RegexPathRule, ExtensionMatchRule, SecretContentRule,
ContentScanConfig, TierOverrides, RuleSet.
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.policy import ContentPattern, ExtensionRule, GlobPattern, RegexRule
from claudenv.domain.policy_rules.interfaces import IContentRule, IExtensionRule, IPathRule
from claudenv.domain.value_objects import Action


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


@dataclass
class ContentScanConfig:
    """Content scanning configuration."""
    enabled: bool = True
    on_match: str = "redact"  # "redact" or "block"
    patterns: list[IContentRule] = None

    def __post_init__(self):
        self.patterns = self.patterns or []


@dataclass
class TierOverrides:
    """Tier-specific policy overrides."""
    extra_deny_extensions: list[str] = None
    extra_deny_paths: list[str] = None
    default_deny: bool = False

    def __post_init__(self):
        self.extra_deny_extensions = self.extra_deny_extensions or []
        self.extra_deny_paths = self.extra_deny_paths or []


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
