"""
claude-env :: Domain - Policy Engine
Pure policy evaluation logic, no I/O dependencies.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claudenv.domain.policy_rules import (
    RuleFactory, RuleSet, ContentScanConfig, PolicyCompiler,
    IPathRule, )
from claudenv.domain.value_objects import (
    Action, Path, RepoSlug, Tier,
)


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Result of a policy evaluation."""
    action: Action
    reason: str
    rule: str = ""

    @classmethod
    def allow(cls, reason: str = "no blocking rule") -> PolicyDecision:
        return cls(Action.ALLOW, reason)

    @classmethod
    def block(cls, reason: str, rule: str = "") -> PolicyDecision:
        return cls(Action.BLOCK, reason, rule)

    @classmethod
    def redact(cls, reason: str, rule: str = "") -> PolicyDecision:
        return cls(Action.REDACT, reason, rule)

    @property
    def is_allowed(self) -> bool:
        return self.action == Action.ALLOW

    @property
    def is_blocked(self) -> bool:
        return self.action == Action.BLOCK

    @property
    def is_redacted(self) -> bool:
        return self.action == Action.REDACT


@dataclass(frozen=True, slots=True)
class ContentScanResult:
    """Result of content scanning."""
    text: str
    hits: list[tuple[str, int]]  # (pattern_name, count), count=-1 means block

    @property
    def is_blocked(self) -> bool:
        return any(count == -1 for _, count in self.hits)

    @property
    def has_redactions(self) -> bool:
        return len(self.hits) > 0 and not self.is_blocked


@dataclass(frozen=True, slots=True)
class CompiledPolicy:
    """Fully compiled policy ready for evaluation."""
    repo: RepoSlug
    tier: Tier
    global_rules: RuleSet
    repo_rules: RuleSet
    content_scan: ContentScanConfig
    override_deny: tuple[IPathRule, ...] = ()

    def evaluate(self, path: Path | str) -> PolicyDecision:
        """Evaluate a path against the compiled policy."""
        rel_path = str(path)

        # 0. Override deny (repo-specific allow-list that beats global/repo deny)
        for rule in self.override_deny:
            if rule.matches(rel_path):
                return PolicyDecision.allow(f"override_deny: {rule.describe()}")

        # 1. Global deny
        decision = self._check_deny(rel_path, self.global_rules, "global")
        if decision:
            return decision

        # 2. Repo deny (includes tier overrides)
        decision = self._check_deny(rel_path, self.repo_rules, "repo")
        if decision:
            return decision

        # 3. Repo allow
        if self._check_allow(rel_path, self.repo_rules):
            return PolicyDecision.allow("matched allow rule")

        # 4. Fall-through
        if self.repo_rules.default_deny:
            return PolicyDecision.block("tier default-deny (not in allowlist)", "default_deny")
        return PolicyDecision.allow("no blocking rule; tier default allow")

    def _check_deny(self, path: str, rules: RuleSet, scope: str) -> PolicyDecision | None:
        for rule in rules.deny_paths:
            if rule.matches(path):
                return PolicyDecision.block(f"{scope} deny path", rule.describe())
        for rule in rules.deny_extensions:
            if rule.matches(path):
                return PolicyDecision.block(f"{scope} deny extension", rule.extension)
        for rule in rules.deny_regex:
            if rule.matches(path):
                return PolicyDecision.block(f"{scope} deny regex: {rule.describe()}", rule.describe())
        return None

    def _check_allow(self, path: str, rules: RuleSet) -> bool:
        for rule in rules.allow_paths:
            if rule.matches(path):
                return True
        for rule in rules.allow_extensions:
            if rule.matches(path):
                return True
        return False

    def scan_content(self, text: str) -> ContentScanResult:
        """Scan content for secrets/PII."""
        if not self.content_scan.enabled:
            return ContentScanResult(text, [])

        hits: list[tuple[str, int]] = []
        out = text
        block_mode = self.content_scan.on_match == "block"

        for pattern in self.content_scan.patterns:
            found = pattern.findall(out)
            if found:
                hits.append((pattern.name, len(found)))
                if block_mode:
                    return "", [(pattern.name, -1)]
                out = pattern.sub(out, f"[REDACTED:{pattern.name}]")

        return out, hits


class PolicyEngine:
    """Pure policy evaluation engine - no I/O, no side effects."""

    def __init__(self, compiled: CompiledPolicy):
        self.compiled = compiled

    @classmethod
    def from_yaml(
            cls,
            global_data: dict[str, Any],
            repo_data: dict[str, Any],
    ) -> PolicyEngine:
        """Create engine from parsed YAML data."""
        compiler = PolicyCompiler()
        global_rules, repo_rules, content_scan = compiler.compile_all(global_data, repo_data)

        repo_slug = RepoSlug.from_string(repo_data.get("repo", "repo"))
        tier = Tier.from_string(repo_data.get("tier", "1"))
        override_deny = tuple(
            RuleFactory().create_glob_rule(p) for p in repo_data.get("override_deny", [])
        )

        compiled = CompiledPolicy(
            repo=repo_slug,
            tier=tier,
            global_rules=global_rules,
            repo_rules=repo_rules,
            content_scan=content_scan,
            override_deny=override_deny,
        )
        return cls(compiled)

    def evaluate_path(self, path: str | Path) -> PolicyDecision:
        """Evaluate a path against the policy."""
        if isinstance(path, str):
            path = Path.from_string(path)
        return self.compiled.evaluate(path)

    def scan_content(self, text: str) -> ContentScanResult:
        """Scan content for secrets/PII."""
        return self.compiled.scan_content(text)

    def get_compiled(self) -> CompiledPolicy:
        return self.compiled
