"""
claude-env :: Domain - Audit Entities - ToolCallProjection
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId


@dataclass(frozen=True, slots=True)
class ToolCallProjection:
    """Tool call projection for querying."""
    event_id: EventId
    ts: str
    tool: str
    args_json: str
    result_kind: str
    duration_ms: int | None
