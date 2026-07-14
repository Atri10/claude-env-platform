"""
claude-env :: Application - Onboarding Service
"""
from __future__ import annotations

from claudenv.application.onboarding.base import OnboardingResult, OnboardingStep, OnboardingContext
from claudenv.application.onboarding.steps import (
    PolicyStep,
    NamespaceStep,
    MCPEnvStep,
    TemplateStep,
    GitHooksStep,
    CommandsStep,
    NativeHooksStep,
    AgentsStep,
    IndexStep,
)
from claudenv.application.onboarding.service import OnboardingService

__all__ = [
    "OnboardingResult",
    "OnboardingStep",
    "OnboardingContext",
    "PolicyStep",
    "NamespaceStep",
    "MCPEnvStep",
    "TemplateStep",
    "GitHooksStep",
    "CommandsStep",
    "NativeHooksStep",
    "AgentsStep",
    "IndexStep",
    "OnboardingService",
]
