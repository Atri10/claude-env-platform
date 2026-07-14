"""
claude-env :: Domain - Content Security Detectors - Verdict
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Verdict:
    flagged: bool
    blocked: bool
    score: float
    reasons: list[str]
