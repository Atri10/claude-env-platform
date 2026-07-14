"""
claude-env :: Domain - Audit Entities - ChainVerificationResult
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId


@dataclass(frozen=True, slots=True)
class ChainVerificationResult:
    """Result of audit chain verification."""
    ok: bool
    broken_at: EventId | None
    total_events: int
