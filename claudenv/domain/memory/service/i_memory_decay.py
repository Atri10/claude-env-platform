"""
claude-env :: Domain - Memory Service (Domain Layer) - IMemoryDecay
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Protocol


class IMemoryDecay(Protocol):
    """Memory decay operations."""

    @abstractmethod
    def decay_all(self, namespace: str) -> int:
        ...
