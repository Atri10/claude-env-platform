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

from claudenv.domain.security._patterns import INJECTION_PATTERNS, SECRET_PATTERNS
from claudenv.domain.security.detectors import (
    PromptInjectionDetector,
    RagPoisonDetector,
    SecretDetector,
    Verdict,
)

__all__ = [
    "INJECTION_PATTERNS",
    "SECRET_PATTERNS",
    "Verdict",
    "PromptInjectionDetector",
    "SecretDetector",
    "RagPoisonDetector",
]
