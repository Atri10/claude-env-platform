"""
claude-env :: DI - In-process event bus

A minimal synchronous pub/sub used for intra-process decoupling. Handler
exceptions are swallowed so one bad subscriber cannot break the publisher.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any


class EventBus:
    """Simple in-process event bus."""

    def __init__(self):
        self._subscribers: dict[str, list[Callable]] = {}

    def publish(self, event_type: str, payload: dict[str, Any]) -> None:
        for handler in self._subscribers.get(event_type, []):
            try:
                handler(payload)
            except Exception:
                pass  # Don't let handler errors break the publisher

    def subscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        self._subscribers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: str, handler: Callable[[dict], None]) -> None:
        if event_type in self._subscribers:
            self._subscribers[event_type] = [h for h in self._subscribers[event_type] if h != handler]
