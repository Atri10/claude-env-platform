"""
claude-env :: Domain - Content Security Detectors

Groups: Verdict, PromptInjectionDetector, SecretDetector, RagPoisonDetector.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from claudenv.domain.security._patterns import INJECTION_PATTERNS, SECRET_PATTERNS


@dataclass
class Verdict:
    flagged: bool
    blocked: bool
    score: float
    reasons: list[str]


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


class SecretDetector:
    """High-recall regex bank for common credential formats."""

    def scan(self, text: str, source: str = "unknown") -> Verdict:
        reasons = [name for name, pat in SECRET_PATTERNS if re.search(pat, text)]
        flagged = bool(reasons)
        return Verdict(flagged, flagged, 1.0 if flagged else 0.0, reasons)

    def redact(self, text: str) -> str:
        out = text
        for name, pat in SECRET_PATTERNS:
            out = re.sub(pat, f"[REDACTED:{name}]", out)
        return out


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
