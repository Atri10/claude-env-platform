"""claude-env :: Domain - Memory - Decay"""
from __future__ import annotations

from claudenv.domain.memory.service.interfaces import IMemoryDecay, IMemoryRepository


class MemoryDecay(IMemoryDecay):
    """Memory decay operations."""

    def __init__(self, repo: IMemoryRepository):
        self.repo = repo

    def decay_all(self, namespace: str) -> int:
        return self.repo.decay_all(namespace)
