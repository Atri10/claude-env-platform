"""
claude-env :: Ports - Event Bus Interface
"""
from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from typing import Any, Protocol


class IEventBus(Protocol):
    """Simple in-process event bus for decoupled notifications."""

    @abstractmethod
    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        ...

    @abstractmethod
    def subscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        ...

    @abstractmethod
    def unsubscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        ...
