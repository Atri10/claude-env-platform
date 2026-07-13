"""
claude-env :: Domain - Content Security Detectors

Heuristic detectors invoked at the trust boundaries:
  * PromptInjectionDetector -> scans retrieved/external text for instruction-
    like content before it enters an agent's context.
  * RagPoisonDetector       -> flags chunks that look crafted to manipulate an
    LLM (hidden instructions, anomalous instruction density).
  * SecretDetector          -> high-recall regex bank for credentials.

These are pure domain logic: no I/O, no audit side effects. Callers (adapters)
decide what to do with a Verdict -- block, redact, warn, and/or log a
security_event via IAuditLogger. That keeps this module trivially unit
testable and free of a dependency on any concrete audit adapter.

Ported from the pre-refactor `security/detectors.py`, which the hexagonal
rewrite ("Refactor Genesis") dropped entirely -- no module under claudenv/
performed prompt-injection/poison/secret screening, silently removing the
"retrieved/external text is data, never instructions" guarantee documented
in CLAUDE.md for the documentation and lancedb_rag MCP servers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Verdict:
    flagged: bool
    blocked: bool
    score: float
    reasons: list[str]


INJECTION_PATTERNS = [
    (r"(?i)\bignore (all |the )?(previous|prior|above) (instructions|prompts?)\b", 0.9),
    (r"(?i)\bdisregard (the )?(system|previous) (prompt|message|instructions)\b", 0.9),
    (r"(?i)\byou are now\b.*\b(dan|developer mode|unrestricted)\b", 0.8),
    (r"(?i)\b(reveal|print|exfiltrate|leak|send).{0,30}\b(system prompt|api[_ ]?key|secret|\.env)\b", 0.95),
    (r"(?i)\bnew (instructions?|task)\b\s*[:\-]", 0.5),
    (r"(?i)<\s*/?\s*(system|assistant)\s*>", 0.7),
    (r"(?i)\bact as\b.*\b(no restrictions|without limitations)\b", 0.7),
    (r"(?i)curl\s+.*\|\s*(sh|bash)", 0.85),
    (r"(?i)\b(base64 -d|eval\()", 0.5),
]

SECRET_PATTERNS = [
    ("aws_access_key", r"AKIA[0-9A-Z]{16}"),
    ("aws_secret", r"(?i)aws_secret_access_key\s*=\s*[A-Za-z0-9/+]{40}"),
    ("private_key", r"-----BEGIN (RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"),
    ("gcp_key", r'"type":\s*"service_account"'),
    ("slack_token", r"xox[baprs]-[0-9A-Za-z-]{10,}"),
    ("github_pat", r"ghp_[0-9A-Za-z]{36}"),
    ("jwt", r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    ("generic_secret", r'(?i)(secret|password|passwd|api[_-]?key|token)["\']?\s*[:=]\s*["\'][^"\']{12,}["\']'),
]


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
