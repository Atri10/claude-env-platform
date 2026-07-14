"""
claude-env :: Domain - Policy Engine - PolicyEngine
"""
from __future__ import annotations

from typing import Any

from claudenv.domain.policy_rules import RuleFactory, PolicyCompiler
from claudenv.domain.value_objects import Path, RepoSlug, Tier
from claudenv.domain.policy_engine.policy_decision import PolicyDecision
from claudenv.domain.policy_engine.content_scan_result import ContentScanResult
from claudenv.domain.policy_engine.compiled_policy import CompiledPolicy


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
