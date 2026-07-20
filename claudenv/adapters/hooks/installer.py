"""
claude-env :: Adapters - Hook Installer

Registers governance hooks and writes them into Claude Code's
``.claude/settings.json`` (repo-local) or ``~/.claude/settings.json`` (global).

Design
------
The installer depends on the hook *abstractions*, never on concrete module
paths. A hook class is registered once with its Claude Code event
(PreToolUse, PostToolUse, SessionStart, SessionEnd); ``install()`` derives the
launch command from the class's own ``__module__`` and its install metadata
(``HOOK_MATCHER`` / ``HOOK_TIMEOUT``). Adding a hook needs no change to
``install()`` (Open/Closed), and every event maps to a *list* of hook entries.
"""
from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claudenv.adapters.hooks.audit_hook import AuditHook
from claudenv.adapters.hooks.policy_hook import PolicyHook
from claudenv.adapters.hooks.session_hook import SessionHook
from claudenv.adapters.hooks.session_metrics_hook import SessionMetricsHook
from claudenv.ports.hooks.interfaces import IPostToolUseHook, IPreToolUseHook

# Default matcher: which tools trigger a hook. Mirrors the legacy
# install_hooks.py behaviour; a hook class may override via HOOK_MATCHER.
DEFAULT_MATCHER = "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash"
DEFAULT_TIMEOUT = 10


@dataclass(frozen=True)
class HookSpec:
    """Immutable description of one installed hook entry."""

    event: str  # PreToolUse | PostToolUse | SessionStart | SessionEnd
    module: str  # importable module path, launched as `python -m <module>`
    matcher: str
    timeout: int


class HookInstaller:
    """Registers governance hooks and installs them into Claude Code settings."""

    def __init__(
        self,
        repo_root: Path | str | None = None,
        python: str | None = None,
    ):
        self.repo_root = Path(repo_root) if repo_root else Path(os.getcwd())
        # Launch hooks with this interpreter. Defaults to the running process so
        # the installed command is portable; override for a pinned venv via
        # the CLAUDE_ENV_HOOK_PYTHON env var.
        self.python = python or os.environ.get("CLAUDE_ENV_HOOK_PYTHON", sys.executable)
        self._specs: list[HookSpec] = []
        self.register_defaults()

    # -- Registration -------------------------------------------------------
    def register_hook(self, hook_cls: type, event: str) -> None:
        """Register a hook class under a Claude Code event.

        ``event`` is one of PreToolUse, PostToolUse, SessionStart, SessionEnd.
        The launch command is derived from the class's own ``__module__`` and
        its ``HOOK_MATCHER`` / ``HOOK_TIMEOUT`` metadata. Adding a hook never
        requires editing ``install()`` (Open/Closed).
        """
        self._specs.append(self._spec_for(hook_cls, event))

    def register_pre_hook(self, hook_cls: type[IPreToolUseHook]) -> None:
        self.register_hook(hook_cls, "PreToolUse")

    def register_post_hook(self, hook_cls: type[IPostToolUseHook]) -> None:
        self.register_hook(hook_cls, "PostToolUse")

    def register_session_hook(self, hook_cls: type, event: str) -> None:
        self.register_hook(hook_cls, event)

    def register_defaults(self) -> None:
        """Register the standard governance + session-cost hooks."""
        self.register_hook(PolicyHook, "PreToolUse")
        self.register_hook(AuditHook, "PostToolUse")
        self.register_hook(SessionMetricsHook, "SessionStart")
        self.register_hook(SessionMetricsHook, "SessionEnd")
        # Session lifecycle hook: records session start/end to the audit ledger.
        self.register_session_hook(SessionHook, "SessionStart")
        self.register_session_hook(SessionHook, "SessionEnd")

    @staticmethod
    def _spec_for(hook_cls: type, event: str) -> HookSpec:
        return HookSpec(
            event=event,
            module=hook_cls.__module__,
            matcher=getattr(hook_cls, "HOOK_MATCHER", DEFAULT_MATCHER),
            timeout=getattr(hook_cls, "HOOK_TIMEOUT", DEFAULT_TIMEOUT),
        )

    # -- Installation -------------------------------------------------------
    def install(self, scope: str = "repo") -> dict[str, Any]:
        """
        Install registered hooks to Claude Code settings.

        scope: "repo" (``<repo>/.claude/settings.json``) or
               "global" (``~/.claude/settings.json``).
        """
        settings_path = self._settings_path(scope)
        settings_path.parent.mkdir(parents=True, exist_ok=True)

        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text())
            except json.JSONDecodeError:
                settings = {}
        else:
            settings = {}

        # Each event maps to a list of hook entries (Claude Code format).
        hooks_config: dict[str, list[dict[str, Any]]] = {}
        for spec in self._specs:
            entry = {
                "matcher": spec.matcher,
                "command": f"{self.python} -m {spec.module}",
                "timeout": spec.timeout,
            }
            hooks_config.setdefault(spec.event, []).append(entry)

        settings["hooks"] = hooks_config
        settings_path.write_text(json.dumps(settings, indent=2))
        return {"installed": True, "path": str(settings_path), "hooks": hooks_config}

    def uninstall(self, scope: str = "repo") -> dict[str, Any]:
        """Remove hooks from settings."""
        settings_path = self._settings_path(scope)
        if not settings_path.exists():
            return {"uninstalled": False, "reason": "no settings file"}

        try:
            settings = json.loads(settings_path.read_text())
            if "hooks" in settings:
                del settings["hooks"]
            settings_path.write_text(json.dumps(settings, indent=2))
            return {"uninstalled": True}
        except Exception as e:  # pragma: no cover - defensive
            return {"uninstalled": False, "error": str(e)}

    def validate(self) -> dict[str, Any]:
        """Validate that PreToolUse/PostToolUse/Session* hooks are present."""
        repo_settings = self.repo_root / ".claude" / "settings.json"
        global_settings = Path.home() / ".claude" / "settings.json"

        results: dict[str, Any] = {"repo": False, "global": False}
        expected = {"PreToolUse", "PostToolUse", "SessionStart", "SessionEnd"}
        for name, path in [("repo", repo_settings), ("global", global_settings)]:
            if path.exists():
                try:
                    settings = json.loads(path.read_text())
                    hooks = settings.get("hooks", {})
                    results[name] = expected.issubset(set(hooks))
                except Exception:
                    results[name] = False
        return results

    # -- Helpers ------------------------------------------------------------
    def _settings_path(self, scope: str) -> Path:
        if scope == "repo":
            return self.repo_root / ".claude" / "settings.json"
        return Path.home() / ".claude" / "settings.json"


def create_installer(
    repo_root: Path | str | None = None, python: str | None = None
) -> HookInstaller:
    return HookInstaller(repo_root, python)


def main() -> None:
    """CLI entry point (``python -m claudenv.adapters.hooks.installer``)."""
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
    elif args.uninstall:
        result = installer.uninstall(scope=args.scope)
    else:
        result = installer.install(scope=args.scope)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
