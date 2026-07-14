"""
claude-env :: Domain - Observability metrics (pure)

Groups the misc pure helpers used by the dashboard and retrieval feedback:
nearest_rank_percentile, parse_window_days, query_hash, usage_boost,
usage_boosts_from_counts, BOOST_UNIT, BOOST_CAP.
"""
from __future__ import annotations

import hashlib
import math

BOOST_UNIT = 0.05
BOOST_CAP = 20


def nearest_rank_percentile(values: list[float], p: float) -> float:
    """p-th percentile (0..100) by nearest rank. Empty -> 0.0."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]


def parse_window_days(window: str) -> float:
    """Return the window length expressed in days.

    '24h' -> 1.0 day; '7d' -> 7 days; '7w' -> 7 days (no 'w' unit); '' -> 30.

    Deliberately crude parsing of strings like '7d' or '24h', matching the
    documented dashboard behavior: extract the digits, default to 30 if none,
    and treat the window as hours only when the string ends in 'h' (any other
    trailing unit, e.g. 'w', silently falls through to days).
    """
    digits = "".join(c for c in window if c.isdigit())
    n = int(digits) if digits else 30
    if window.strip().endswith("h"):
        return n / 24.0
    return float(n)


def query_hash(query: str) -> str:
    """sha256(query)[:16] — a grouping key, not a cryptographic commitment.

    Truncated sha256 used only to group repeated identical queries in
    rag_chunk_feedback. Not a security control.
    """
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]


def usage_boost(n: int) -> float:
    """Boost for a single chunk given its count of 'used' signals.

    Turns historical 'used' counts per chunk into a small additive reranking
    boost.

        boost(n) = BOOST_UNIT * ln(1 + min(n, BOOST_CAP))

    Strictly increasing with sharply diminishing returns; capped at
    n=BOOST_CAP. There is no negative/down-weighting case — chunks are never
    penalized, they simply fail to accumulate a boost.
    """
    return BOOST_UNIT * math.log1p(min(n, BOOST_CAP))


def usage_boosts_from_counts(counts: dict[str, int]) -> dict[str, float]:
    """Map chunk_id -> boost from a chunk_id -> used-count map."""
    return {chunk_id: usage_boost(n) for chunk_id, n in counts.items()}
