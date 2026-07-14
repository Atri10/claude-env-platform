"""
claude-env :: Ports - MCP servers configuration interface
"""
from __future__ import annotations

from abc import abstractmethod
from typing import Any, Protocol


class IMCPConfig(Protocol):
    """MCP servers configuration."""

    @abstractmethod
    def get_mcp_servers_config(self) -> dict[str, Any]:
        ...
