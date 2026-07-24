"""
claude-env :: Ports - Audit repository interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol

from claudenv.domain.audit import ChainVerificationResult, EventType
from claudenv.domain.value_objects import EventId, RepoSlug, SessionId, Tier


class IAuditRepository(Protocol):
    """Audit event persistence."""

    @abstractmethod
    def append(
            self,
            event_type: EventType,
            payload: dict[str, Any],
            session_id: SessionId,
            actor: str,
            repo: RepoSlug | None,
            tier: Tier | None,
            projection: tuple[str, dict[str, Any]] | None = None,
    ) -> EventId:
        ...

    @abstractmethod
    def get_chain(self) -> list[dict[str, Any]]:
        ...

    @abstractmethod
    def verify_chain(self) -> ChainVerificationResult:
        ...
