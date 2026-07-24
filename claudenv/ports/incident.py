"""
claude-env :: Ports - Incident Store

Consumer-owned interface for the incident kill-switch state. The domain layer
uses ``IncidentState`` (pure data) and a boolean; the I/O (reading/writing the
filesystem marker) lives behind this port in an adapter.
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol

from claudenv.domain.incident import IncidentState


class IIncidentStore(Protocol):
    """Read/write the incident-mode marker (filesystem-backed in production)."""

    @abstractmethod
    def is_active(self) -> bool:
        """True when the INCIDENT marker exists — operations must be denied."""
        ...

    @abstractmethod
    def read(self) -> IncidentState:
        """Full incident state (reason, by, since) or inactive."""
        ...

    @abstractmethod
    def write(self, reason: str, by: str) -> IncidentState:
        """Arm incident mode and return the resulting state."""
        ...

    @abstractmethod
    def clear(self) -> None:
        """Disarm incident mode (remove the marker)."""
        ...
