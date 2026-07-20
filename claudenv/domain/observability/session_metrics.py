"""
claude-env :: Domain - Session cost computation (pure)

Token-usage -> estimated USD cost. Mirrors the pricing model described in
docs/superpowers/specs/2026-07-10-session-cost-tracking-design.md; the single
``"default"`` entry is the intentional, config-free placeholder the spec
calls out as pre-existing.
"""
from __future__ import annotations

# (input $/1M tokens, output $/1M tokens) per model.
PRICES: dict[str, tuple[float, float]] = {
    "default": (3.00, 15.00),
}


def compute_cost(input_tokens: int, output_tokens: int, model: str = "default") -> float:
    """Estimated USD cost for a session's cumulative token usage."""
    pin, pout = PRICES.get(model, PRICES["default"])
    return (input_tokens * pin + output_tokens * pout) / 1_000_000
