"""
claude-env :: Domain - Audit Entities - SecurityProjection
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId


@dataclass(frozen=True, slots=True)
class SecurityProjection:
    """Security event projection."""
    event_id: EventId
    ts: str
    category: str
    severity: str
    detail: str
    source: str | None
