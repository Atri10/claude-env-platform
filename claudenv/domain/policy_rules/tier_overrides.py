"""
claude-env :: Domain - Policy Rule Strategies - TierOverrides
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TierOverrides:
    """Tier-specific policy overrides."""
    extra_deny_extensions: list[str] = None
    extra_deny_paths: list[str] = None
    default_deny: bool = False

    def __post_init__(self):
        self.extra_deny_extensions = self.extra_deny_extensions or []
        self.extra_deny_paths = self.extra_deny_paths or []
