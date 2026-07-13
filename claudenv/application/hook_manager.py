"""
claude-env :: Application - Hook Manager
"""
from __future__ import annotations

from typing import List

from claudenv.ports.hooks.interfaces import IPreToolUseHook, IPostToolUseHook

# Global hook registry
_pre_hooks: List[IPreToolUseHook] = []
_post_hooks: List[IPostToolUseHook] = []
_installed = False


class HookManager:
    """Central manager for governance hooks."""

    @staticmethod
    def install_hooks(hooks: List[IPreToolUseHook | IPostToolUseHook]) -> None:
        """Install hooks into the global registry."""
        global _pre_hooks, _post_hooks, _installed
        for hook in hooks:
            if isinstance(hook, IPreToolUseHook):
                if hook not in _pre_hooks:
                    _pre_hooks.append(hook)
            elif isinstance(hook, IPostToolUseHook):
                if hook not in _post_hooks:
                    _post_hooks.append(hook)
        _installed = True

    @staticmethod
    def uninstall_hooks(hooks: List[IPreToolUseHook | IPostToolUseHook]) -> None:
        """Uninstall hooks from the global registry."""
        global _pre_hooks, _post_hooks, _installed
        for hook in hooks:
            if isinstance(hook, IPreToolUseHook) and hook in _pre_hooks:
                _pre_hooks.remove(hook)
            elif isinstance(hook, IPostToolUseHook) and hook in _post_hooks:
                _post_hooks.remove(hook)
        _installed = len(_pre_hooks) > 0 or len(_post_hooks) > 0

    @staticmethod
    def validate_hooks(hooks: List[IPreToolUseHook | IPostToolUseHook]) -> bool:
        """Validate that hooks are properly configured."""
        if not hooks:
            return False
        for hook in hooks:
            if not (isinstance(hook, IPreToolUseHook) or isinstance(hook, IPostToolUseHook)):
                return False
        return True

    @staticmethod
    def get_pre_hooks() -> List[IPreToolUseHook]:
        """Get all pre-tool hooks."""
        return _pre_hooks.copy()

    @staticmethod
    def get_post_hooks() -> List[IPostToolUseHook]:
        """Get all post-tool hooks."""
        return _post_hooks.copy()

    @staticmethod
    def clear() -> None:
        """Clear all hooks (for testing)."""
        global _pre_hooks, _post_hooks, _installed
        _pre_hooks.clear()
        _post_hooks.clear()
        _installed = False

    @staticmethod
    def is_installed() -> bool:
        """Check if hooks are installed."""
        return _installed
