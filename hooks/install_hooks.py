#!/usr/bin/env python3
"""
claude-env :: hook installer
File: hooks/install_hooks.py
Purpose:
    Idempotently register the claude-env policy + audit hooks in a Claude Code
    settings file. The hooks make the policy engine govern Claude Code's NATIVE
    tools, not just MCP traffic.

    By DEFAULT the target is the current repo's committed project settings
    (`<repo>/.claude/settings.json`), so governance is scoped to onboarded repos
    only — an un-onboarded repo on the same machine is left untouched. The old
    machine-wide behavior is still available behind `--global`.

Portability:
    The committed `.claude/settings.json` is shared across a team, so the hook
    command must not bake in the onboarding machine's absolute paths. We write it
    with the literal `$CLAUDE_ENV_HOME` env var (quoted), which Claude Code's
    shell expands per machine. Any dev with claude-env bootstrapped and
    CLAUDE_ENV_HOME set (or the default ~/.claude-env) runs the right hook.

Usage:
    python hooks/install_hooks.py                 # install into <cwd>/.claude/settings.json
    python hooks/install_hooks.py --repo /path    # install into /path/.claude/settings.json
    python hooks/install_hooks.py --global        # install into ~/.claude/settings.json
    python hooks/install_hooks.py --uninstall
    python hooks/install_hooks.py --settings /path/to/settings.json
    python hooks/install_hooks.py --dry-run       # print resulting JSON only
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

GLOBAL_SETTINGS = Path.home() / ".claude" / "settings.json"

PRE_MATCHER = "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash|WebFetch|WebSearch"
POST_MATCHER = "Write|Edit|NotebookEdit|Bash"
SESSION_MATCHER = "*"  # every SessionStart source / SessionEnd reason
# Portable, per-machine: the shell running the hook expands $CLAUDE_ENV_HOME.
# Quoted so a home dir with spaces survives. Keep in sync with the legacy
# suffix match in _strip_cmd so old absolute-path installs are also removable.
_PY = '"$CLAUDE_ENV_HOME/venv/bin/python"'
PRE_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/policy_hook.py"'
POST_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/audit_hook.py"'
SESSION_CMD = f'{_PY} "$CLAUDE_ENV_HOME/hooks/session_metrics_hook.py"'


def _entry(matcher: str, command: str) -> dict:
    return {"matcher": matcher, "hooks": [{"type": "command", "command": command}]}


def _has_cmd(entries: list, command: str) -> bool:
    for e in entries:
        for h in e.get("hooks", []):
            if h.get("command") == command:
                return True
    return False


def _is_ours(command: str, script: str) -> bool:
    """True if `command` is a claude-env install of `script` (e.g.
    'hooks/policy_hook.py'), in EITHER the new portable env-var form or a legacy
    absolute-path form. Used by uninstall so we can clean up both eras."""
    cmd = command or ""
    return cmd.strip().rstrip('"').endswith(script)


def _strip_cmd(entries: list, script: str) -> list:
    """Drop any hook whose command invokes our `script` (portable or legacy)."""
    out = []
    for e in entries:
        hooks = [h for h in e.get("hooks", []) if not _is_ours(h.get("command", ""), script)]
        if hooks:
            out.append({**e, "hooks": hooks})
    return out


def _default_settings(args) -> Path:
    """Resolve the target settings file from the flags.
    Precedence: --settings > --global > --repo/<cwd>/.claude/settings.json."""
    if args.settings:
        return Path(args.settings).expanduser()
    if args.global_:
        return GLOBAL_SETTINGS
    repo = Path(args.repo).expanduser() if args.repo else Path.cwd()
    return repo / ".claude" / "settings.json"


def main() -> int:
    ap = argparse.ArgumentParser(description="Install/remove claude-env Claude Code hooks")
    ap.add_argument("--settings", default=None,
                    help="explicit settings.json path (overrides --repo/--global)")
    ap.add_argument("--repo", default=None,
                    help="repo whose .claude/settings.json to target (default: cwd)")
    ap.add_argument("--global", dest="global_", action="store_true",
                    help="target the machine-wide ~/.claude/settings.json instead")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    spath = _default_settings(args)
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
    sess_start = hooks.setdefault("SessionStart", [])
    sess_end = hooks.setdefault("SessionEnd", [])

    if args.uninstall:
        hooks["PreToolUse"] = _strip_cmd(pre, "hooks/policy_hook.py")
        hooks["PostToolUse"] = _strip_cmd(post, "hooks/audit_hook.py")
        hooks["SessionStart"] = _strip_cmd(sess_start, "hooks/session_metrics_hook.py")
        hooks["SessionEnd"] = _strip_cmd(sess_end, "hooks/session_metrics_hook.py")
        action = "removed from"
    else:
        if not _has_cmd(pre, PRE_CMD):
            pre.append(_entry(PRE_MATCHER, PRE_CMD))
        if not _has_cmd(post, POST_CMD):
            post.append(_entry(POST_MATCHER, POST_CMD))
        if not _has_cmd(sess_start, SESSION_CMD):
            sess_start.append(_entry(SESSION_MATCHER, SESSION_CMD))
        if not _has_cmd(sess_end, SESSION_CMD):
            sess_end.append(_entry(SESSION_MATCHER, SESSION_CMD))
        action = "installed into"

    rendered = json.dumps(settings, indent=2) + "\n"
    if args.dry_run:
        print(rendered)
        return 0

    spath.parent.mkdir(parents=True, exist_ok=True)
    spath.write_text(rendered)
    print(f"claude-env hooks {action} {spath}")
    print("Restart Claude Code (or start a new session) for hooks to take effect.")
    if not args.uninstall:
        # The command is portable ($CLAUDE_ENV_HOME); warn only if this machine
        # can't resolve it, so a fresh clone knows to bootstrap.
        home = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
        py = home / "venv" / "bin" / "python"
        if not py.exists():
            print(f"WARN: venv python not found at {py} — run bootstrap.py first, "
                  f"or the hook command will fail open on this machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
