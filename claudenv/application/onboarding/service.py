"""
claude-env :: Application - Onboarding Service - OnboardingService
"""
from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path

from claudenv.application.onboarding.base import OnboardingContext, OnboardingResult, OnboardingStep
from claudenv.application.onboarding.steps import (
    AgentsStep,
    CommandsStep,
    GitHooksStep,
    IndexStep,
    MCPEnvStep,
    NamespaceStep,
    NativeHooksStep,
    PolicyStep,
    TemplateStep,
)
from claudenv.domain.value_objects import BranchName, RepoSlug, Tier
from claudenv.ports import IConfigProvider

logger = logging.getLogger(__name__)


class OnboardingService:
    """Orchestrates the onboarding process."""

    def __init__(self, config: IConfigProvider):
        self.config = config
        self.steps: list[OnboardingStep] = [
            PolicyStep(config),
            NamespaceStep(config),
            MCPEnvStep(config),
            TemplateStep(config),
            GitHooksStep(config),
            CommandsStep(config),
            NativeHooksStep(config),
            AgentsStep(config),
            IndexStep(config),
        ]

    def onboard(
            self,
            repo_root: str,
            slug: str | None = None,
            tier: int | None = None,
            branch: str | None = None,
            description: str = "",
            dry_run: bool = False,
            force_policy: bool = False,
            force_template: bool = False,
            no_template: bool = False,
            no_post_commit: bool = False,
    ) -> OnboardingResult:
        repo_path = Path(repo_root).resolve()
        if not repo_path.is_dir():
            raise ValueError(f"Repo path does not exist: {repo_root}")

        # Detect/slug
        slug = RepoSlug.generate(slug or repo_path.name)
        branch = BranchName.from_string(branch or self._detect_branch(repo_path))
        tier = Tier.from_string(str(tier)) if tier is not None else self._detect_tier(repo_path)

        ctx = OnboardingContext(
            repo_root=str(repo_path),
            slug=slug,
            tier=tier,
            branch=branch,
            description=description,
            dry_run=dry_run,
            force_policy=force_policy,
            force_template=force_template,
            no_template=no_template,
            no_post_commit=no_post_commit,
        )

        print("\n=== claude-env onboarding ===")
        print(f"  repo: {repo_path}")
        print(f"  slug: {slug}")
        print(f"  tier: {int(tier)} ({tier.label})")
        print(f"  branch: {branch}")

        for step in self.steps:
            if step.can_run(ctx):
                try:
                    step.execute(ctx)
                except Exception as e:
                    logger.warning("onboarding step %s failed: %s", step.__class__.__name__, e)

        return OnboardingResult(
            repo_root=str(repo_path),
            slug=slug,
            tier=tier,
            branch=branch,
            rag_table=ctx.rag_table,
            memory_namespace=ctx.memory_ns,
            memory_isolated=ctx.memory_isolated,
            steps_completed=ctx.steps_completed,
            steps_skipped=ctx.steps_skipped,
        )

    def _detect_branch(self, repo: Path) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
                capture_output=True, text=True, timeout=10,
            ).stdout.strip()
            return out or "main"
        except Exception:
            logger.warning("branch detection failed; defaulting to main", exc_info=True)
            return "main"

    def _detect_tier(self, repo: Path) -> Tier:
        policy = repo / ".claude" / "repo-policy.yaml"
        if policy.exists():
            for line in policy.read_text().splitlines():
                m = re.match(r"\s*tier\s*:\s*([0-3])\b", line)
                if m:
                    return Tier.parse(int(m.group(1)))
        return Tier.INTERNAL
