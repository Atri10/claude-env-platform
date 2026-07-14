"""
claude-env :: Ports - Approval gate verdict value object
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import Tier


@dataclass
class GateVerdict:
    """Result of an approval gate evaluation."""
    required: bool
    agent: str
    action: str
    target: str | None
    tier: Tier | None
    reasons: list[str] = ()

    @property
    def summary(self) -> str:
        tgt = f" -> {self.target}" if self.target else ""
        return f"{self.agent}:{self.action}{tgt} (tier={self.tier})"
