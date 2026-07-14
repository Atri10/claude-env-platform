"""
claude-env :: Domain - Policy Entities
"""
from __future__ import annotations

from claudenv.domain.policy.compiled_policy import CompiledPolicy
from claudenv.domain.policy.global_policy import GlobalPolicy
from claudenv.domain.policy.policy_engine import PolicyEngine
from claudenv.domain.policy.policy_service import PolicyService
from claudenv.domain.policy.repo_policy import RepoPolicy
from claudenv.domain.policy.results import ContentScanResult, PolicyDecision

# Re-exported so `from claudenv.domain.policy import X` keeps working exactly
# as it did when policy.py imported these from value_objects directly.
from claudenv.domain.value_objects import (
    Action,
    ContentPattern,
    ContentScanConfig,
    ExtensionRule,
    GlobPattern,
    Path,
    PolicyRuleSet,
    RegexRule,
    RepoSlug,
    Tier,
)

__all__ = [
    "Action",
    "ContentPattern",
    "Path",
    "GlobPattern",
    "RegexRule",
    "RepoSlug",
    "Tier",
    "ExtensionRule",
    "PolicyRuleSet",
    "ContentScanConfig",
    "PolicyDecision",
    "ContentScanResult",
    "CompiledPolicy",
    "RepoPolicy",
    "GlobalPolicy",
    "PolicyEngine",
    "PolicyService",
]
