"""
claude-env :: Ports - Hook interfaces
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


class IPostToolUseHook(ABC):
    """Hook called after a tool completes."""

    @abstractmethod
    def on_tool_complete(self, tool_outcome: Dict[str, Any]) -> None:
        """
        Process tool outcome after execution.

        Args:
            tool_outcome: Dict with tool_name, success, details, duration_ms
        """
        pass

    def record_decision(self, approval: Dict[str, Any]) -> None:
        """Record approval/denial decision."""
        pass

    def on_session_start(self, session_id: str) -> Dict[str, Any]:
        """Called when session starts."""
        return {}

    def on_session_end(self, session_id: str) -> None:
        """Called when session ends."""
        pass

    def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> Dict[str, Any]:
        """Restore session from checkpoint."""
        return {}


class HookInstaller(ABC):
    """Abstract hook installer."""

    @abstractmethod
    def install(self, scope: str = "repo") -> dict:
        """Install hooks."""
        pass

    @abstractmethod
    def uninstall(self, scope: str = "repo") -> dict:
        """Uninstall hooks."""
        pass

    @abstractmethod
    def validate(self) -> dict:
        """Validate installation."""
        pass
