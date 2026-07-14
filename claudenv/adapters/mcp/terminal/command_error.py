"""
claude-env :: Adapters - Terminal MCP Server - Command parsing error
"""
from __future__ import annotations


class _CommandError(Exception):
    """Unsupported shell feature in command string."""
    pass
