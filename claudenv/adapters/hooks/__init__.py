"""
claude-env :: Adapters - Hooks
"""
from __future__ import annotations

from claudenv.ports.hooks.interfaces import IPostToolUseHook, IPreToolUseHook

from .audit_hook import AuditHook
from .audit_hook import create_hook as create_audit_hook
from .installer import HookInstaller, create_installer
from .policy_hook import PolicyHook
from .policy_hook import create_hook as create_policy_hook

__all__ = [
    "PolicyHook",
    "create_policy_hook",
    "AuditHook",
    "create_audit_hook",
    "HookInstaller",
    "create_installer",
    "IPreToolUseHook",
    "IPostToolUseHook",
]
