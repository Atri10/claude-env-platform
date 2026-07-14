"""
claude-env :: Domain - Content Security Detectors - PromptInjectionDetector
"""
from __future__ import annotations

import re

from claudenv.domain.security._patterns import INJECTION_PATTERNS
from claudenv.domain.security.verdict import Verdict


class PromptInjectionDetector:
    """Scans text for instruction-like content (defence-in-depth, not a guarantee)."""

    def __init__(self, block_threshold: float = 0.8):
        self.block_threshold = block_threshold

    def scan(self, text: str, source: str = "unknown") -> Verdict:
        score, reasons = 0.0, []
        for pat, w in INJECTION_PATTERNS:
            if re.search(pat, text):
                score = max(score, w)
                reasons.append(pat[:40])
        flagged = score > 0.0
        blocked = score >= self.block_threshold
        return Verdict(flagged, blocked, score, reasons)
