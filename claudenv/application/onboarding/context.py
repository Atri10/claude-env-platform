"""
claude-env :: Application - Onboarding Service - OnboardingContext
"""
from __future__ import annotations

from claudenv.domain.value_objects import RepoSlug, Tier, BranchName


class OnboardingContext:
    """Context passed between onboarding steps."""

    def __init__(
            self,
            repo_root: str,
            slug: RepoSlug,
            tier: Tier,
            branch: BranchName,
            description: str,
            dry_run: bool = False,
            force_policy: bool = False,
            force_template: bool = False,
            no_template: bool = False,
            no_post_commit: bool = False,
    ):
        self.repo_root = repo_root
        self.slug = slug
        self.tier = tier
        self.branch = branch
        self.description = description
        self.dry_run = dry_run
        self.force_policy = force_policy
        self.force_template = force_template
        self.no_template = no_template
        self.no_post_commit = no_post_commit

        self.steps_completed: list[str] = []
        self.steps_skipped: list[str] = []

    def add_step(self, name: str, skipped: bool = False) -> None:
        if skipped:
            self.steps_skipped.append(name)
        else:
            self.steps_completed.append(name)
