"""
claude-env :: Domain - Value Objects - ContentScanConfig
"""
from __future__ import annotations

from dataclasses import dataclass, field

from claudenv.domain.value_objects.content_pattern import ContentPattern


@dataclass(frozen=True, slots=True)
class ContentScanConfig:
    """Content scanning configuration."""
    enabled: bool = True
    on_match: str = "redact"  # "redact" or "block"
    patterns: list[ContentPattern] = field(default_factory=list)
