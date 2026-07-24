"""
claude-env :: Application - Policy - PolicyService

Application service for policy operations. Moved from domain/policy/ to
application/policy/ because it performs I/O (yaml file reads, git ls-files
subprocess) which violates the domain-purity invariant.

The domain layer keeps only pure policy objects: PolicyEngine,
CompiledPolicy, RepoPolicy, GlobalPolicy.
"""
from __future__ import annotations

import logging
import subprocess
from pathlib import Path as _FsPath
from typing import Any

from claudenv.adapters.incident import FileIncidentStore
from claudenv.domain.policy.global_policy import GlobalPolicy
from claudenv.domain.policy.policy_engine import PolicyEngine
from claudenv.domain.policy.repo_policy import RepoPolicy
from claudenv.domain.value_objects import RepoSlug
from claudenv.ports import IConfigProvider

logger = logging.getLogger(__name__)


class PolicyService:
    """Application service for policy operations."""

    def __init__(self, config: IConfigProvider):
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
        incident_active = FileIncidentStore().is_active()
        return PolicyEngine(
            repo_policy.to_compiled(global_policy),
            incident_active=incident_active,
        )

    def _enumerate_files(self, repo_root: str) -> list[str]:
        """List the repo's tracked files (git ls-files, fallback to rglob)."""
        try:
            out = subprocess.run(
                ["git", "-C", repo_root, "ls-files"],
                capture_output=True, text=True, timeout=10, check=True,
            ).stdout.strip()
            files = out.splitlines() if out else []
        except Exception:
            logger.warning("git ls-files failed; falling back to rglob", exc_info=True)
            files = [
                str(p.relative_to(repo_root))
                for p in _FsPath(repo_root).rglob("*")
                if p.is_file()
            ]
        return files

    def simulate(self, repo_root: str, candidate: dict[str, Any]) -> dict[str, Any]:
        """Dry-run a candidate repo policy and report what would change.

        Compares the current compiled policy against the candidate over the
        repo's tracked files. Nothing is written and no agent session is
        affected -- this is pure "what would change".
        """
        global_policy = self.get_global_policy()
        current_repo = self.get_repo_policy(repo_root)
        if current_repo is None:
            current_repo = RepoPolicy(
                version=1,
                tier=global_policy.tier,
                repo=RepoSlug.from_string(_FsPath(repo_root).name),
            )
        candidate_repo = RepoPolicy.from_yaml(candidate)
        incident_active = FileIncidentStore().is_active()
        current_engine = PolicyEngine(
            current_repo.to_compiled(global_policy),
            incident_active=incident_active,
        )
        candidate_engine = PolicyEngine(
            candidate_repo.to_compiled(global_policy),
            incident_active=incident_active,
        )

        files = self._enumerate_files(repo_root)
        newly_blocked: list[tuple[str, str, str]] = []
        newly_allowed: list[tuple[str, str, str]] = []
        changed_rule: list[tuple[str, str, str]] = []
        blocked_current = blocked_candidate = 0
        for f in files:
            cur = current_engine.evaluate_path(f)
            can = candidate_engine.evaluate_path(f)
            if not cur.is_allowed:
                blocked_current += 1
            if not can.is_allowed:
                blocked_candidate += 1
            if cur.is_allowed and not can.is_allowed:
                newly_blocked.append((f, can.reason, can.rule))
            elif (not cur.is_allowed) and can.is_allowed:
                newly_allowed.append((f, can.reason, can.rule))
            elif (not cur.is_allowed) and (not can.is_allowed) and cur.rule != can.rule:
                changed_rule.append((f, cur.rule, can.rule))

        return {
            "repo": str(current_repo.repo),
            "tier_current": int(current_repo.tier),
            "tier_candidate": int(candidate_repo.tier),
            "files": len(files),
            "blocked_current": blocked_current,
            "blocked_candidate": blocked_candidate,
            "newly_blocked": newly_blocked,
            "newly_allowed": newly_allowed,
            "changed_rule": changed_rule,
        }
