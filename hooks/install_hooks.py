#!/usr/bin/env python3
"""
claude-env :: hook installer
File: hooks/install_hooks.py
Purpose:
    Idempotently register the claude-env policy + audit hooks in a Claude Code
    settings file (default: ~/.claude/settings.json). The hooks make the policy
    engine govern Claude Code's NATIVE tools, not just MCP traffic.

Usage:
    python hooks/install_hooks.py                 # install into user settings
    python hooks/install_hooks.py --uninstall
    python hooks/install_hooks.py --settings /path/to/settings.json
    python hooks/install_hooks.py --dry-run       # print resulting JSON only
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
PY = HOME / "venv" / "bin" / "python"

PRE_MATCHER = "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash"
POST_MATCHER = "Write|Edit|NotebookEdit|Bash"
PRE_CMD = f"{PY} {HOME / 'hooks' / 'policy_hook.py'}"
POST_CMD = f"{PY} {HOME / 'hooks' / 'audit_hook.py'}"


def _entry(matcher: str, command: str) -> dict:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}


def _has_cmd(entries: list, command: str) -> bool:
    for e in entries:
        for h in e.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def _strip_cmd(entries: list, command: str) -> list:
    out = []
    for e in entries:
        hooks = [h for h in e.get("hooks", []) if h.get("command") != command]
        if hooks:
            out.append({**e, "hooks": hooks})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="Install/remove claude-env Claude Code hooks")
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"))
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    spath = Path(args.settings).expanduser()
    settings = {}
    if spath.exists():
        try:
            settings = json.loads(spath.read_text())
        except Exception as exc:
            print(f"ERROR: cannot parse {spath}: {exc}")
            return 1

    hooks = settings.setdefault("hooks", {})
    pre = hooks.setdefault("PreToolUse", [])
    post = hooks.setdefault("PostToolUse", [])

    if args.uninstall:
        hooks["PreToolUse"] = _strip_cmd(pre, PRE_CMD)
        hooks["PostToolUse"] = _strip_cmd(post, POST_CMD)
        action = "removed from"
    else:
        if not _has_cmd(pre, PRE_CMD):
            pre.append(_entry(PRE_MATCHER, PRE_CMD))
        if not _has_cmd(post, POST_CMD):
            post.append(_entry(POST_MATCHER, POST_CMD))
        action = "installed into"

    rendered = json.dumps(settings, indent=2) + "\n"
    if args.dry_run:
        print(rendered)
        return 0

    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(rendered)
    print(f"claude-env hooks {action} {spath}")
    print("Restart Claude Code (or start a new session) for hooks to take effect.")
    if not args.uninstall and not PY.exists():
        print(f"WARN: venv python not found at {PY} — run bootstrap.py first, "
              f"or the hook command will fail open.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
