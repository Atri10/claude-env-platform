"""
claude-env :: Domain - Audit Entities - PolicyViolationProjection
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId


@dataclass(frozen=True, slots=True)
class PolicyViolationProjection:
    """Policy violation projection."""
    event_id: EventId
    ts: str
    repo: str
    tier: int | None
    path: str
    rule: str
    decision: str
    actor: str
