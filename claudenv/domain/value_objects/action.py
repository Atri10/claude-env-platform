"""
claude-env :: Domain - Value Objects - Action
"""
from __future__ import annotations

from enum import Enum


class Action(str, Enum):
    """Policy decision actions."""
    ALLOW = "allow"
    BLOCK = "block"
    REDACT = "redact"
