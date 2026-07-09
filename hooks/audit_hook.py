#!/usr/bin/env python3
"""
claude-env :: Claude Code PostToolUse audit hook
File: hooks/audit_hook.py
Purpose:
    Write a tool_call audit row for every state-mutating native tool call
    (Write, Edit, NotebookEdit, Bash) so the tamper-evident ledger covers
    Claude Code's own actions, not just MCP traffic. Read-only tools are
    skipped by default to keep ledger volume sane; set
    CLAUDE_ENV_HOOK_AUDIT_ALL=true to audit everything intercepted.

Protocol: stdin JSON {session_id, cwd, tool_name, tool_input, tool_response}.
Never blocks anything (PostToolUse); never fails loudly.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (HOME, Path(__file__).resolve().parents[1]):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_MUTATING = {"Write", "Edit", "NotebookEdit", "Bash"}
_AUDIT_ALL = os.environ.get("CLAUDE_ENV_HOOK_AUDIT_ALL", "").lower() == "true"
_MAX_ARG = 400  # truncate long args so the ledger stays compact


def _repo_slug(cwd: str) -> str | None:
    """The onboarded repo slug for cwd, so audit rows key-match the RAG/memory
    namespaces. Falls back to the directory basename if cwd is not inside an
    onboarded repo — the hook only ever fires inside a repo that installed it
    (repo-local hooks), so this fallback is a defensive label, not a signal
    that un-onboarded repos are being treated as governed."""
    from lib.repo_policy import repo_slug
    return repo_slug(cwd, fallback_to_basename=True)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        tool = payload.get("tool_name", "")
        if tool not in _MUTATING and not _AUDIT_ALL:
            return 0
        tin = payload.get("tool_input") or {}
        resp = payload.get("tool_response")
        cwd = payload.get("cwd", "")
        session = payload.get("session_id", "hook")

        summary = {}
        for k in ("file_path", "path", "notebook_path", "command", "pattern"):
            if tin.get(k):
                summary[k] = str(tin[k])[:_MAX_ARG]
        ok = True
        if isinstance(resp, dict) and (resp.get("is_error") or resp.get("error")):
            ok = False

        from audit.audit_logger import AuditLogger
        AuditLogger(session, actor="claude-code",
                    repo=_repo_slug(cwd)).tool_call(
            tool=f"native.{tool}", args=summary,
            result_kind="ok" if ok else "error")
    except Exception:
        pass  # auditing must never disturb the session
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
