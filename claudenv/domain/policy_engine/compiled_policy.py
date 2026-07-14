"""
claude-env :: Domain - Policy Engine - CompiledPolicy
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.policy_rules import RuleSet, ContentScanConfig, IPathRule
from claudenv.domain.value_objects import Path, RepoSlug, Tier
from claudenv.domain.policy_engine.policy_decision import PolicyDecision
from claudenv.domain.policy_engine.content_scan_result import ContentScanResult


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
