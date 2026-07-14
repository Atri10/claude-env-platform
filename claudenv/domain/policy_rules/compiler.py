"""
claude-env :: Domain - Policy Rule Strategies - Compiler

Groups: RuleFactory, PolicyCompiler.
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.policy_rules.interfaces import IPathRule, IExtensionRule, IContentRule
from claudenv.domain.policy_rules.rules import (
    GlobPathRule,
    RegexPathRule,
    ExtensionMatchRule,
    SecretContentRule,
    RuleSet,
    ContentScanConfig,
)


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
