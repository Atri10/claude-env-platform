"""
claude-env :: Domain - Memory Service (Domain Layer) - MemoryDecay
"""
from __future__ import annotations

from claudenv.domain.memory.service.i_memory_decay import IMemoryDecay
from claudenv.domain.memory.service.i_memory_repository import IMemoryRepository


class MemoryDecay(IMemoryDecay):
    """Memory decay operations."""

    def __init__(self, repo: IMemoryRepository):
        self.repo = repo

    def decay_all(self, namespace: str) -> int:
        return self.repo.decay_all(namespace)
