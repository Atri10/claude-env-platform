"""
claude-env :: Adapters - Configuration - MCP Servers
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claudenv.ports import IMCPConfig

from .base import _ConfigBase


class MCPConfigProvider(_ConfigBase, IMCPConfig):
    """MCP servers configuration provider."""

    def get_mcp_servers_config(self) -> dict[str, Any]:
        config_path = Path(self.get_claude_env_home()) / "config" / "mcp-servers.json"
        if config_path.exists():
            return json.loads(config_path.read_text())
        return {}
