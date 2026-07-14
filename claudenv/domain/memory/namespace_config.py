"""
claude-env :: Domain - Memory Entities - NamespaceConfig
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import Tier


@dataclass(frozen=True, slots=True)
class NamespaceConfig:
    """Configuration for a memory namespace."""
    name: str
    isolated: bool = False
    tier: Tier = Tier.INTERNAL
    shared_with: tuple[str, ...] = ()
