"""
claude-env :: Domain - Content Security Detectors - SecretDetector
"""
from __future__ import annotations

import re

from claudenv.domain.security._patterns import SECRET_PATTERNS
from claudenv.domain.security.verdict import Verdict


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
