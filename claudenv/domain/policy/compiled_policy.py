"""
claude-env :: Domain - Policy Entities - CompiledPolicy
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import (
    GlobPattern, Path, PolicyRuleSet, ContentScanConfig, RepoSlug, Tier,
)
from claudenv.domain.policy.results import PolicyDecision


@dataclass(frozen=True, slots=True)
class CompiledPolicy:
    """Fully compiled policy ready for evaluation."""
    repo: RepoSlug
    tier: Tier
    global_rules: PolicyRuleSet
    repo_rules: PolicyRuleSet
    content_scan: ContentScanConfig
    override_deny: tuple[GlobPattern, ...] = ()

    def evaluate(self, path: Path | str) -> PolicyDecision:
        """Evaluate a path against the compiled policy."""
        rel_path = str(path)

        # 0. Override deny (repo-specific allow-list that beats global/repo deny)
        for pattern in self.override_deny:
            if pattern.matches(rel_path):
                return PolicyDecision.allow(f"override_deny: {pattern}")

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

    def _check_deny(self, path: str, rules: PolicyRuleSet, scope: str) -> PolicyDecision | None:
        for pattern in rules.deny_paths:
            if pattern.matches(path):
                return PolicyDecision.block(f"{scope} deny path", str(pattern))
        for ext_rule in rules.deny_extensions:
            if ext_rule.matches(path):
                return PolicyDecision.block(f"{scope} deny extension", ext_rule.extension)
        for regex in rules.deny_regex:
            if regex.matches(path):
                return PolicyDecision.block(f"{scope} deny regex: {regex.reason}", regex.pattern.pattern)
        return None

    def _check_allow(self, path: str, rules: PolicyRuleSet) -> bool:
        for pattern in rules.allow_paths:
            if pattern.matches(path):
                return True
        for ext_rule in rules.allow_extensions:
            if ext_rule.matches(path):
                return True
        return False

    def scan_content(self, text: str) -> tuple[str, list[tuple[str, int]]]:
        """Scan content for secrets/PII. Returns (redacted_text, hits)."""
        if not self.content_scan.enabled:
            return text, []

        hits: list[tuple[str, int]] = []
        out = text
        block_mode = self.content_scan.on_match == "block"

        for pattern in self.content_scan.patterns:
            found = pattern.findall(out)
            if found:
                hits.append((pattern.name, len(found)))
                if block_mode:
                    return "", [(pattern.name, -1)]  # -1 signals full block
                out = pattern.sub(out, f"[REDACTED:{pattern.name}]")

        return out, hits
