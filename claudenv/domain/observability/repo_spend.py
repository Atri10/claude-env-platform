"""
claude-env :: Domain - Month-to-date spend for a single repo
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RepoSpend:
    """Month-to-date spend for a single repo (input to budget evaluation)."""

    repo: str
    sessions: int
    input_tokens: int
    output_tokens: int
    spent_usd: float
