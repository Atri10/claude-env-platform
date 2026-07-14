"""
claude-env :: Domain - Audit Entities - AgentActionProjection
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId


@dataclass(frozen=True, slots=True)
class AgentActionProjection:
    """Agent action projection."""
    event_id: EventId
    ts: str
    agent: str
    action: str
    target: str | None
    summary: str | None
    success: bool
