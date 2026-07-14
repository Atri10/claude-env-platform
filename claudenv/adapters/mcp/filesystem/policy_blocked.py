"""
claude-env :: Adapters - Filesystem Policy MCP Server - PolicyBlocked exception
"""
from __future__ import annotations


class PolicyBlocked(Exception):
    """Raised when policy denies an operation."""
    pass
