"""
claude-env :: Domain - Percentile (pure)

Nearest-rank percentile over an in-memory list. Not a streaming/approximate
estimator — matches the dashboard's `_pct` helper exactly.
"""
from __future__ import annotations


def nearest_rank_percentile(values: list[float], p: float) -> float:
    """p-th percentile (0..100) by nearest rank. Empty -> 0.0."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((p / 100) * (len(s) - 1)))))
    return s[k]
