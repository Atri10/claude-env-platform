"""
claude-env :: Ports - Hook Interfaces

Consumer-owned interfaces for governance hooks.
"""
from __future__ import annotations

from claudenv.ports.hooks.interfaces.interfaces import (
    HookInstaller,
    IPostToolUseHook,
    IPreToolUseHook,
)

__all__ = [
    "IPreToolUseHook",
    "IPostToolUseHook",
    "HookInstaller",
]
