"""
claude-env :: Domain - Memory Entities - effective_confidence
"""
from __future__ import annotations

from datetime import datetime

from claudenv.domain.value_objects import utc_now


def effective_confidence(stored: float, half_life: float, updated_at: str | datetime) -> float:
    """Calculate time-decayed confidence."""
    if isinstance(updated_at, str):
        updated_at = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    age_days = max(0.0, (utc_now() - updated_at).total_seconds() / 86400)
    return stored * (2 ** (-age_days / max(1e-6, half_life)))
