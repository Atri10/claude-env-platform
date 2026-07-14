"""
claude-env :: Domain - Content Security Detectors - RagPoisonDetector
"""
from __future__ import annotations

import re

from claudenv.domain.security.prompt_injection_detector import PromptInjectionDetector
from claudenv.domain.security.verdict import Verdict


class RagPoisonDetector:
    """Flag chunks crafted to manipulate the model: prompt-injection hits plus
    high density of imperative/instruction tokens relative to chunk length."""

    def __init__(self):
        self._pid = PromptInjectionDetector()

    def scan_chunk(self, text: str, source: str) -> Verdict:
        v = self._pid.scan(text, source)
        imperative = len(re.findall(r"(?i)\b(ignore|disregard|you must|always|never|instead) \b", text))
        density = imperative / max(1, len(text.split()))
        score = max(v.score, min(1.0, density * 10))
        flagged = score >= 0.4
        reasons = v.reasons + (["high_density"] if density > 0.05 else [])
        return Verdict(flagged, score >= 0.8, score, reasons)
