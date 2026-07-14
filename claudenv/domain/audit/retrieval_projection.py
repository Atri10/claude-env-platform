"""
claude-env :: Domain - Audit Entities - RetrievalProjection
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId


@dataclass(frozen=True, slots=True)
class RetrievalProjection:
    """Retrieval event projection."""
    event_id: EventId
    ts: str
    repo: str
    branch: str | None
    query: str
    top_k: int
    returned: int
    reranked: bool
    duration_ms: int | None
