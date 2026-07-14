"""
claude-env :: Domain - Audit Entities - HumanApprovalProjection
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId, RequestId


@dataclass(frozen=True, slots=True)
class HumanApprovalProjection:
    """Human approval projection."""
    request_id: RequestId
    event_id: EventId
    ts: str
    agent: str
    repo: str
    tier: int | None
    action: str
    decision: str
    decided_by: str | None
    decided_at: str | None
