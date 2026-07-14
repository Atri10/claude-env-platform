"""
claude-env :: Domain - Policy Entities - PolicyService
"""
from __future__ import annotations

from pathlib import Path as _FsPath
from typing import Any

from claudenv.domain.policy.global_policy import GlobalPolicy
from claudenv.domain.policy.policy_engine import PolicyEngine
from claudenv.domain.policy.repo_policy import RepoPolicy
from claudenv.domain.value_objects import RepoSlug


class PolicyService:
    """Application service for policy operations."""

    def __init__(self, config: Any):  # IConfigProvider
        self._config = config

    def get_global_policy(self) -> GlobalPolicy:
        data = self._config.get_global_policy()
        return GlobalPolicy.from_yaml(data)

    def get_repo_policy(self, repo_root: str) -> RepoPolicy | None:
        repo_yaml = _FsPath(repo_root) / ".claude" / "repo-policy.yaml"
        if not repo_yaml.exists():
            return None
        import yaml
        data = yaml.safe_load(repo_yaml.read_text()) or {}
        return RepoPolicy.from_yaml(data)

    def load_engine(self, repo_root: str) -> PolicyEngine:
        global_policy = self.get_global_policy()
        repo_policy = self.get_repo_policy(repo_root)
        if not repo_policy:
            # No repo-policy.yaml yet — compile a default repo policy (tier
            # from global defaults, no repo-specific rules) against the same
            # RepoPolicy.to_compiled() path a real one uses, rather than a
            # separate compile function that could drift from it.
            repo_policy = RepoPolicy(
                version=1, tier=global_policy.tier,
                repo=RepoSlug.from_string(_FsPath(repo_root).name),
            )
        return PolicyEngine(repo_policy.to_compiled(global_policy))

    def simulate(self, repo_root: str, candidate: dict[str, Any]) -> dict[str, Any]:
        """Simulate a candidate policy."""
        # Load current policy
        current = self.get_repo_policy(repo_root)
        global_policy = self.get_global_policy()

        # Compile both. Compiling the candidate validates it (to_compiled can
        # raise on a malformed policy); we don't need to bind the result.
        current_compiled = current.to_compiled(global_policy) if current else None
        candidate_policy = RepoPolicy.from_yaml(candidate)
        candidate_policy.to_compiled(global_policy)

        # Test paths against both
        # This is a simplified version - real implementation would test more paths
        return {
            "current": "compiled" if current_compiled else "none",
            "candidate": "compiled",
            "diff": "simulated",
        }

    def diff(self, repo_root: str, candidate: dict[str, Any]) -> dict[str, Any]:
        """Diff current vs candidate policy."""
        current = self.get_repo_policy(repo_root)
        return {
            "has_current": current is not None,
            "candidate_keys": list(candidate.keys()),
        }
