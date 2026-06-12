#!/usr/bin/env python3
"""
claude-env :: Claude Code PreToolUse policy hook
File: hooks/policy_hook.py
Purpose:
    Extend policy enforcement to Claude Code's NATIVE tools (Read, Write, Edit,
    Glob, Grep, NotebookEdit, Bash). The MCP `filesystem-policy` server only
    governs agents that go through MCP; this hook closes the gap so every file
    access in any Claude Code session is policy-checked and audited.

Wiring (done by hooks/install_hooks.py):
    ~/.claude/settings.json -> hooks.PreToolUse:
        matcher: "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash"
        command: $CLAUDE_ENV_HOME/venv/bin/python $CLAUDE_ENV_HOME/hooks/policy_hook.py

Protocol (Claude Code hooks):
    stdin : JSON {session_id, cwd, tool_name, tool_input, ...}
    stdout: empty (allow) OR JSON hookSpecificOutput with permissionDecision
            'deny'/'ask' + reason. Always exit 0 (the decision is in the JSON).

Decisions:
    * incident mode marker present            -> deny EVERYTHING (fail-closed)
    * path matches a policy block rule        -> deny + policy_violation audit
    * Write/Edit content contains a secret    -> ask (operator confirms)
    * otherwise                               -> allow (silent)

Failure posture:
    Internal errors default to ALLOW (so a broken hook cannot brick the editor),
    unless CLAUDE_ENV_HOOK_FAIL_CLOSED=true — recommended for tier 2+ machines.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
if str(HOME) not in sys.path:
    sys.path.insert(0, str(HOME))
# also allow running from the repo checkout (e.g. during validation)
_REPO = Path(__file__).resolve().parents[1]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

INCIDENT_MARKER = HOME / "state" / "INCIDENT"
FAIL_CLOSED = os.environ.get("CLAUDE_ENV_HOOK_FAIL_CLOSED", "").lower() == "true"

# tool_input keys that carry a filesystem path, per native tool
_PATH_KEYS = ("file_path", "path", "notebook_path")
# tools whose tool_input carries content being written
_WRITE_CONTENT_KEYS = ("content", "new_string", "new_source")


def _deny(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))


def _ask(reason: str) -> None:
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "ask",
        "permissionDecisionReason": reason}}))


def _repo_root(cwd: str) -> Path:
    """Walk up from cwd to the enclosing repo (policy file or .git), else cwd."""
    p = Path(cwd or ".").resolve()
    for cand in (p, *p.parents):
        if (cand / ".claude" / "repo-policy.yaml").exists() or (cand / ".git").exists():
            return cand
    return p


def _rel_for_policy(path_str: str, root: Path) -> str:
    """Path as the policy engine expects: repo-relative when inside the repo,
    otherwise the absolute path with the leading '/' stripped so global
    '**/...' deny globs (e.g. **/.ssh/**) still match."""
    p = Path(os.path.expanduser(path_str))
    if not p.is_absolute():
        return str(p)
    try:
        return str(p.resolve().relative_to(root))
    except ValueError:
        return str(p.resolve()).lstrip("/")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0  # malformed input: nothing to decide on

    tool = payload.get("tool_name", "")
    tin = payload.get("tool_input") or {}
    cwd = payload.get("cwd", os.getcwd())
    session = payload.get("session_id", "hook")

    # 1. incident mode: hard stop for every intercepted tool, including Bash.
    if INCIDENT_MARKER.exists():
        try:
            detail = json.loads(INCIDENT_MARKER.read_text()).get("reason", "")
        except Exception:
            detail = ""
        _deny(f"claude-env incident mode is active{': ' + detail if detail else ''}. "
              f"Run 'claude-env incident off' to lift it.")
        return 0

    # 2. collect the path (if any) this tool call touches
    path_str = next((tin[k] for k in _PATH_KEYS if tin.get(k)), None)
    if not path_str and tool == "Grep":
        path_str = tin.get("path")
    if not path_str and tool not in ("Write", "Edit", "NotebookEdit"):
        return 0  # Bash and pathless calls: no path policy to apply

    try:
        from security.policy_engine import PolicyEngine
        root = _repo_root(cwd)
        engine = PolicyEngine.load(root)

        if path_str:
            rel = _rel_for_policy(path_str, root)
            decision = engine.evaluate_path(rel)
            if decision.action == "block":
                try:
                    from audit.audit_logger import AuditLogger
                    AuditLogger(session, actor="claude-code", repo=root.name,
                                tier=engine.repo.tier).policy_violation(
                        rel, decision.rule or decision.reason, "block",
                        tier=engine.repo.tier)
                except Exception:
                    pass  # auditing must never break the decision itself
                _deny(f"blocked by claude-env policy ({decision.reason}: "
                      f"{decision.rule or rel})")
                return 0

        # 3. secret scan on content being written — surface to the operator
        if tool in ("Write", "Edit", "NotebookEdit"):
            content = " ".join(str(tin.get(k, "")) for k in _WRITE_CONTENT_KEYS)
            if content.strip():
                from security.detectors import SECRET_PATTERNS
                import re
                hits = [n for n, p in SECRET_PATTERNS if re.search(p, content)]
                if hits:
                    try:
                        from audit.audit_logger import AuditLogger
                        AuditLogger(session, actor="claude-code",
                                    repo=root.name).security_event(
                            "secret", "high",
                            f"secret pattern in outgoing write: {hits}",
                            source=path_str or tool)
                    except Exception:
                        pass
                    _ask(f"claude-env: content being written matches secret "
                         f"pattern(s) {hits} — confirm this write")
                    return 0
    except Exception as exc:  # engine/DB unavailable
        if FAIL_CLOSED:
            _deny(f"claude-env policy hook error (fail-closed): {exc}")
        # fail-open: stay silent -> allow
        return 0

    return 0  # allow


if __name__ == "__main__":
    raise SystemExit(main())
