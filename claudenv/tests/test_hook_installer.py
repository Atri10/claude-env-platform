"""
Tests for the registration-driven ``HookInstaller``.

The installer must depend on the hook *abstractions* (not hardcoded module
paths): registering a hook class drives ``install()`` to derive the launch
command from the class's own ``__module__`` and its ``HOOK_MATCHER`` /
``HOOK_TIMEOUT`` metadata.
"""
from __future__ import annotations

import json

from claudenv.adapters.hooks.audit_hook import AuditHook
from claudenv.adapters.hooks.installer import HookInstaller
from claudenv.adapters.hooks.policy_hook import PolicyHook
from claudenv.ports.hooks.interfaces import IPreToolUseHook


class TestHookInstaller:
    def test_install_writes_valid_settings(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        result = HookInstaller(repo).install()

        assert result["installed"] is True
        settings = json.loads((repo / ".claude" / "settings.json").read_text())
        hooks = settings["hooks"]

        # Claude Code expects a LIST of entries per event.
        assert isinstance(hooks["PreToolUse"], list)
        assert isinstance(hooks["PostToolUse"], list)

        pre = hooks["PreToolUse"][0]
        assert "claudenv.adapters.hooks.policy_hook" in pre["hooks"][0]["command"]
        assert pre["matcher"] == PolicyHook.HOOK_MATCHER
        assert pre["hooks"][0]["timeout"] == PolicyHook.HOOK_TIMEOUT

        post = hooks["PostToolUse"][0]
        assert "claudenv.adapters.hooks.audit_hook" in post["hooks"][0]["command"]
        assert post["hooks"][0]["timeout"] == AuditHook.HOOK_TIMEOUT

    def test_register_custom_hook_drives_install(self, tmp_path):
        # A new hook is added by registration only -- no edit to install().
        class MyPre(IPreToolUseHook):
            HOOK_MATCHER = "Bash"
            HOOK_TIMEOUT = 7

            def on_tool_call(self, tool_call):
                return {"allow": True}

        repo = tmp_path / "repo"
        repo.mkdir()
        installer = HookInstaller(repo)
        installer._specs.clear()
        installer.register_pre_hook(MyPre)

        result = installer.install()
        pre = result["hooks"]["PreToolUse"][0]
        # The command is derived from the registered class's own module.
        assert pre["hooks"][0]["command"].endswith(f"-m {MyPre.__module__}")
        assert pre["matcher"] == "Bash"
        assert pre["hooks"][0]["timeout"] == 7

    def test_uninstall_removes_hooks(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        installer = HookInstaller(repo)
        installer.install()
        res = installer.uninstall()

        assert res["uninstalled"] is True
        settings = json.loads((repo / ".claude" / "settings.json").read_text())
        assert "hooks" not in settings

    def test_reinstall_is_idempotent(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        HookInstaller(repo).install()
        HookInstaller(repo).install()

        settings = json.loads((repo / ".claude" / "settings.json").read_text())
        assert len(settings["hooks"]["PreToolUse"]) == 1
        assert len(settings["hooks"]["PostToolUse"]) == 1
