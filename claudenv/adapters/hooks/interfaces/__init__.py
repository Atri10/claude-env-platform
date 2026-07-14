"""
claude-env :: Adapters - Hook Interfaces

Consumer-owned interfaces for governance hooks.
"""
from __future__ import annotations

from .pre_tool_use import IPreToolUseHook
from .post_tool_use import IPostToolUseHook
from .hook_installer import HookInstaller

__all__ = [
    "IPreToolUseHook",
    "IPostToolUseHook",
    "HookInstaller",
]
