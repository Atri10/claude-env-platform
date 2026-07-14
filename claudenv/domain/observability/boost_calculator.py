"""
claude-env :: Domain - Retrieval feedback boost (pure)

Turns historical 'used' counts per chunk into a small additive reranking boost.

Formula (verified against docs/guide/observability-budgets.md):

    boost(n) = BOOST_UNIT * ln(1 + min(n, BOOST_CAP))

Strictly increasing with sharply diminishing returns; capped at n=BOOST_CAP.
There is no negative/down-weighting case — chunks are never penalized, they
simply fail to accumulate a boost.
"""
from __future__ import annotations

import math

BOOST_UNIT = 0.05
BOOST_CAP = 20


def usage_boost(n: int) -> float:
    """Boost for a single chunk given its count of 'used' signals."""
    return BOOST_UNIT * math.log1p(min(n, BOOST_CAP))


def usage_boosts_from_counts(counts: dict[str, int]) -> dict[str, float]:
    """Map chunk_id -> boost from a chunk_id -> used-count map."""
    return {chunk_id: usage_boost(n) for chunk_id, n in counts.items()}
