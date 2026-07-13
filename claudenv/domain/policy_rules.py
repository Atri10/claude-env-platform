"""
claude-env :: Domain - Policy Rule Strategies
Strategy pattern for different rule types.
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from claudenv.domain.policy import GlobPattern, RegexRule, ExtensionRule, ContentPattern
from claudenv.domain.value_objects import Action


# ============================================================================
# Rule Interfaces
# ============================================================================

class IPathRule(ABC):
    """Interface for path-matching rules."""

    @abstractmethod
    def matches(self, path: str) -> bool:
        ...

    @abstractmethod
    def describe(self) -> str:
        ...


class IExtensionRule(ABC):
    """Interface for extension-matching rules."""

    @abstractmethod
    def matches(self, path: str) -> bool:
        ...

    @property
    @abstractmethod
    def extension(self) -> str:
        ...


class IContentRule(ABC):
    """Interface for content-scanning rules."""

    @abstractmethod
    def findall(self, text: str) -> list[str]:
        ...

    @abstractmethod
    def sub(self, text: str, replacement: str) -> str:
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        ...


# ============================================================================
# Path Rule Implementations
# ============================================================================

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


# ============================================================================
# Extension Rule Implementation
# ============================================================================

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


# ============================================================================
# Content Rule Implementation
# ============================================================================

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


# ============================================================================
# Rule Registry / Factory
# ============================================================================

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


# ============================================================================
# Rule Collections
# ============================================================================

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


# ============================================================================
# Policy Compiler
# ============================================================================

class PolicyCompiler:
    """Compile YAML configuration into executable policy rules."""

    def __init__(self, factory: RuleFactory | None = None):
        self.factory = factory or RuleFactory()

    def compile_global_policy(self, data: dict[str, Any]) -> RuleSet:
        """Compile global policy from YAML."""
        rules = RuleSet()

        # Global deny paths
        for p in data.get("deny", {}).get("paths", []):
            rules.deny_paths.append(self.factory.create_glob_rule(p))

        # Global deny extensions
        for e in data.get("deny", {}).get("extensions", []):
            rules.deny_extensions.append(self.factory.create_extension_rule(e))

        # Global deny regex
        for r in data.get("deny", {}).get("regex", []):
            pattern = r["pattern"] if isinstance(r, dict) else r
            reason = r.get("reason", "regex") if isinstance(r, dict) else "regex"
            rules.deny_regex.append(self.factory.create_regex_rule(pattern, reason))

        # Global allow paths
        for p in data.get("allow", {}).get("paths", []):
            rules.allow_paths.append(self.factory.create_glob_rule(p))

        # Global allow extensions
        for e in data.get("allow", {}).get("extensions", []):
            rules.allow_extensions.append(self.factory.create_extension_rule(e))

        return rules

    def compile_repo_policy(self, data: dict[str, Any], tier_overrides: dict[str, Any]) -> RuleSet:
        """Compile repo policy with tier overrides."""
        rules = RuleSet()

        # Repo deny paths
        for p in data.get("deny", {}).get("paths", []):
            rules.deny_paths.append(self.factory.create_glob_rule(p))

        # Repo deny extensions
        for e in data.get("deny", {}).get("extensions", []):
            rules.deny_extensions.append(self.factory.create_extension_rule(e))

        # Repo deny regex
        for r in data.get("deny", {}).get("regex", []):
            pattern = r["pattern"] if isinstance(r, dict) else r
            reason = r.get("reason", "regex") if isinstance(r, dict) else "regex"
            rules.deny_regex.append(self.factory.create_regex_rule(pattern, reason))

        # Repo allow paths
        for p in data.get("allow", {}).get("paths", []):
            rules.allow_paths.append(self.factory.create_glob_rule(p))

        # Repo allow extensions
        for e in data.get("allow", {}).get("extensions", []):
            rules.allow_extensions.append(self.factory.create_extension_rule(e))

        # Override deny (repo-only escape hatch)
        for p in data.get("override_deny", []):
            rules.override_paths.append(self.factory.create_glob_rule(p))

        # Tier overrides
        tier = data.get("tier", 1)
        tier_data = tier_overrides.get(tier, tier_overrides.get(str(tier), {}))

        for e in tier_data.get("extra_deny_extensions", []):
            rules.deny_extensions.append(self.factory.create_extension_rule(e))
        for p in tier_data.get("extra_deny_paths", []):
            rules.deny_paths.append(self.factory.create_glob_rule(p))

        rules.default_deny = tier_data.get("default_deny", False)

        return rules

    def compile_content_scan(self, data: dict[str, Any]) -> ContentScanConfig:
        """Compile content scanning configuration."""
        cs = data.get("content_scan", {})
        config = ContentScanConfig(
            enabled=cs.get("enabled", True),
            on_match=cs.get("on_match", "redact"),
        )
        for p in cs.get("patterns", []):
            config.patterns.append(self.factory.create_content_rule(p["name"], p["pattern"]))
        return config

    def compile_all(
            self,
            global_data: dict[str, Any],
            repo_data: dict[str, Any],
    ) -> tuple[RuleSet, RuleSet, ContentScanConfig]:
        """Compile both global and repo policies."""
        global_rules = self.compile_global_policy(global_data)
        repo_rules = self.compile_repo_policy(repo_data, global_data.get("tiers", {}))
        content_scan = self.compile_content_scan(repo_data) if repo_data.get("content_scan") else \
            self.compile_content_scan(global_data)
        return global_rules, repo_rules, content_scan
