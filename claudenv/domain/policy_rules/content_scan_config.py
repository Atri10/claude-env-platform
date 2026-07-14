"""
claude-env :: Domain - Policy Rule Strategies - ContentScanConfig
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.policy_rules.i_content_rule import IContentRule


@dataclass
class ContentScanConfig:
    """Content scanning configuration."""
    enabled: bool = True
    on_match: str = "redact"  # "redact" or "block"
    patterns: list[IContentRule] = None

    def __post_init__(self):
        self.patterns = self.patterns or []
