"""
claude-env :: Domain - Value Objects - Time helpers
"""
from __future__ import annotations

from datetime import datetime, timezone


def utc_now() -> datetime:
    """Current UTC time with timezone info."""
    return datetime.now(timezone.utc)


def iso_now() -> str:
    """Current UTC time as ISO 8601 string."""
    return utc_now().isoformat().replace("+00:00", "Z")
