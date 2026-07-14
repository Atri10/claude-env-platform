"""
claude-env :: Adapters - Hook Interfaces - PreToolUse

Consumer-owned interfaces for governance hooks.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict


class IPreToolUseHook(ABC):
    """Hook called before a tool is executed."""

    @abstractmethod
    def on_tool_call(self, tool_call: Dict[str, Any]) -> Dict[str, Any]:
        """
        Evaluate tool call before execution.

        Args:
            tool_call: Dict with 'tool' (str) and 'args' (dict)

        Returns:
            {"allow": True} or {"allow": False, "reason": "..."} or {"allow": "ask", "reason": "..."}
        """
        pass
