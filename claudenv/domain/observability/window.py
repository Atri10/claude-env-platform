"""
claude-env :: Domain - Dashboard time-window parsing (pure)

Deliberately crude parsing of strings like '7d' or '24h', matching the
documented dashboard behavior: extract the digits, default to 30 if none,
and treat the window as hours only when the string ends in 'h' (any other
trailing unit, e.g. 'w', silently falls through to days).
"""
from __future__ import annotations


def parse_window_days(window: str) -> float:
    """Return the window length expressed in days.

    '24h' -> 1.0 day; '7d' -> 7 days; '7w' -> 7 days (no 'w' unit); '' -> 30.
    """
    digits = "".join(c for c in window if c.isdigit())
    n = int(digits) if digits else 30
    if window.strip().endswith("h"):
        return n / 24.0
    return float(n)
