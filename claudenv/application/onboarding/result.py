"""
claude-env :: Application - Onboarding Service - OnboardingResult
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import RepoSlug, Tier, BranchName


@dataclass
class OnboardingResult:
    repo_root: str
    slug: RepoSlug
    tier: Tier
    branch: BranchName
    rag_table: str
    memory_namespace: str
    memory_isolated: bool
    steps_completed: list[str]
    steps_skipped: list[str]
