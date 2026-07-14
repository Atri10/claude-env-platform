"""
claude-env :: Domain - Value Objects - Time helpers
"""
from __future__ import annotations

from datetime import UTC, datetime


def utc_now() -> datetime:
    """Current UTC time with timezone info."""
    return datetime.now(UTC)


def iso_now() -> str:
    """Current UTC time as ISO 8601 string."""
    return utc_now().isoformat().replace("+00:00", "Z")
