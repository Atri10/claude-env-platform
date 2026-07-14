"""
claude-env :: Domain - Policy Engine - ContentScanResult
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ContentScanResult:
    """Result of content scanning."""
    text: str
    hits: list[tuple[str, int]]  # (pattern_name, count), count=-1 means block

    @property
    def is_blocked(self) -> bool:
        return any(count == -1 for _, count in self.hits)

    @property
    def has_redactions(self) -> bool:
        return len(self.hits) > 0 and not self.is_blocked
