"""
claude-env :: Adapters - Hook Interfaces

Consumer-owned interfaces for governance hooks.
"""
from __future__ import annotations

from .hooks import HookInstaller, IPostToolUseHook, IPreToolUseHook

__all__ = [
    "IPreToolUseHook",
    "IPostToolUseHook",
    "HookInstaller",
]
