"""
claude-env :: Adapters - Hooks
"""
from __future__ import annotations

from .audit_hook import AuditHook, create_hook as create_audit_hook
from .installer import HookInstaller, create_installer
from .interfaces import IPreToolUseHook, IPostToolUseHook
from .policy_hook import PolicyHook, create_hook as create_policy_hook
from .session_hook import SessionHook, create_hook as create_session_hook

__all__ = [
    "PolicyHook",
    "create_policy_hook",
    "AuditHook",
    "create_audit_hook",
    "SessionHook",
    "create_session_hook",
    "HookInstaller",
    "create_installer",
    "IPreToolUseHook",
    "IPostToolUseHook",
]
