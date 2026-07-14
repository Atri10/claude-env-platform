"""
claude-env :: Application - Onboarding Service - OnboardingStep base class
"""
from __future__ import annotations

from claudenv.ports import IConfigProvider

from claudenv.application.onboarding.context import OnboardingContext


class OnboardingStep:
    """Base class for onboarding steps."""

    def __init__(self, config: IConfigProvider):
        self.config = config

    def execute(self, ctx: OnboardingContext) -> bool:
        """Execute the step. Returns True if step ran, False if skipped."""
        raise NotImplementedError

    def can_run(self, ctx: OnboardingContext) -> bool:
        """Check if step should run."""
        return True
