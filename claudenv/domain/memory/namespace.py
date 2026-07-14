"""
claude-env :: Domain - Memory Entities - Namespace
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import RepoSlug


@dataclass(frozen=True, slots=True)
class Namespace:
    """Memory namespace with isolation settings."""
    name: str
    isolated: bool = False
    shared_with: tuple[str, ...] = ()

    @classmethod
    def project(cls, slug: RepoSlug) -> Namespace:
        return cls(f"proj-{slug}", isolated=False)

    @classmethod
    def agent(cls, agent_id: str) -> Namespace:
        return cls(f"agent:{agent_id}", isolated=True)

    @classmethod
    def global_ns(cls) -> Namespace:
        return cls("global", isolated=False)

    def can_read(self, other: Namespace) -> bool:
        if self.isolated or other.isolated:
            return self.name == other.name or other.name in self.shared_with
        return True
