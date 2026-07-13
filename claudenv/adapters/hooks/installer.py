"""
claude-env :: Adapters - Hook Installer

Manages registration and installation of governance hooks.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any, List

from claudenv.adapters.config import get_config
from claudenv.ports.hooks.interfaces import IPreToolUseHook, IPostToolUseHook


class HookInstaller:
    """Manages hook registration and installation."""

    def __init__(self, repo_root: Path | str | None = None):
        self.repo_root = Path(repo_root) if repo_root else Path(os.getcwd())
        self.config = get_config()
        self._pre_hooks: List[IPreToolUseHook] = []
        self._post_hooks: List[IPostToolUseHook] = []

    def register_pre_hook(self, hook: IPreToolUseHook) -> None:
        if hook not in self._pre_hooks:
            self._pre_hooks.append(hook)

    def register_post_hook(self, hook: IPostToolUseHook) -> None:
        if hook not in self._post_hooks:
            self._post_hooks.append(hook)

    def install(self, scope: str = "repo") -> dict:
        """
        Install hooks to Claude Code settings.

        scope: "repo" (per-repo .claude/settings.json) or "global" (~/.claude/settings.json)
        """
        if scope == "repo":
            settings_path = self.repo_root / ".claude" / "settings.json"
        else:
            settings_path = Path.home() / ".claude" / "settings.json"

        settings_path.parent.mkdir(parents=True, exist_ok=True)

        # Load existing or create new
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
            except json.JSONDecodeError:
                settings = {}
        else:
            settings = {}

        # Build hooks config
        hooks_config = {
            "PreToolUse": {
                "matcher": "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash",
                "command": f"{os.environ.get('CLAUDE_ENV_HOME', '~/.claude-env')}/venv/bin/python -m claudenv.adapters.hooks.policy_hook",
                "timeout": 10,
            },
            "PostToolUse": {
                "matcher": "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash",
                "command": f"{os.environ.get('CLAUDE_ENV_HOME', '~/.claude-env')}/venv/bin/python -m claudenv.adapters.hooks.audit_hook",
                "timeout": 5,
            },
        }

        settings["hooks"] = hooks_config
        settings_path.write_text(json.dumps(settings, indent=2))

        return {"installed": True, "path": str(settings_path)}

    def uninstall(self, scope: str = "repo") -> dict:
        """Remove hooks from settings."""
        if scope == "repo":
            settings_path = self.repo_root / ".claude" / "settings.json"
        else:
            settings_path = Path.home() / ".claude" / "settings.json"

        if not settings_path.exists():
            return {"uninstalled": False, "reason": "no settings file"}

        try:
            settings = json.loads(settings_path.read_text())
            if "hooks" in settings:
                del settings["hooks"]
            settings_path.write_text(json.dumps(settings, indent=2))
            return {"uninstalled": True}
        except Exception as e:
            return {"uninstalled": False, "error": str(e)}

    def validate(self) -> dict:
        """Validate hook installation."""
        repo_settings = self.repo_root / ".claude" / "settings.json"
        global_settings = Path.home() / ".claude" / "settings.json"

        results = {"repo": False, "global": False}

        for name, path in [("repo", repo_settings), ("global", global_settings)]:
            if path.exists():
                try:
                    settings = json.loads(path.read_text())
                    hooks = settings.get("hooks", {})
                    results[name] = "PreToolUse" in hooks and "PostToolUse" in hooks
                except Exception:
                    results[name] = False

        return results


def create_installer(repo_root: Path | str | None = None) -> HookInstaller:
    return HookInstaller(repo_root)


def main() -> None:
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="claude-env hook installer")
    parser.add_argument("--repo", type=str, help="Repository root (default: cwd)")
    parser.add_argument("--scope", choices=["repo", "global"], default="repo")
    parser.add_argument("--uninstall", action="store_true", help="Uninstall hooks")
    parser.add_argument("--validate", action="store_true", help="Validate installation")

    args = parser.parse_args()

    repo_root = Path(args.repo) if args.repo else Path.cwd()
    installer = HookInstaller(repo_root)

    if args.validate:
        result = installer.validate()
        print(json.dumps(result, indent=2))
    elif args.uninstall:
        result = installer.uninstall(scope=args.scope)
        print(json.dumps(result, indent=2))
    else:
        result = installer.install(scope=args.scope)
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
