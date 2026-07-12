from typing import List
from claudenv.ports.hooks import HookInstaller
from claudenv.ports.hooks import IPreToolUseHook, IPostToolUseHook

class HookInstaller(HookInstaller):
    def __init__(self):
        self._hooks: List[IPreToolUseHook | IPostToolUseHook] = []
        self._registered = False

    def register_hook(self, hook: IPreToolUseHook | IPostToolUseHook) -> None:
        """
        Register a hook for tool lifecycle events
        """
        if hook not in self._hooks:
            self._hooks.append(hook)

    def unregister_hook(self, hook: IPreToolUseHook | IPostToolUseHook) -> None:
        """
        Remove a previously registered hook
        """
        if hook in self._hooks:
            self._hooks.remove(hook)

    def list_hooks(self) -> List[IPreToolUseHook | IPostToolUseHook]:
        """
        Return list of currently registered hooks
        """
        return self._hooks.copy()

    def install_hooks(self) -> None:
        """
        Install all registered hooks into the system
        """
        if not self._registered:
            from claudenv.application.hook_manager import HookManager
            HookManager.install_hooks(self._hooks)
            self._registered = True

    def uninstall_hooks(self) -> None:
        """
        Remove all registered hooks from the system
        """
        if self._registered:
            from claudenv.application.hook_manager import HookManager
            HookManager.uninstall_hooks(self._hooks)
            self._registered = False

    def validate_installation(self) -> bool:
        """
        Validate that hooks are properly installed and functional
        """
        if not self._registered:
            return False

        from claudenv.application.hook_manager import HookManager
        return HookManager.validate_hooks(self._hooks)