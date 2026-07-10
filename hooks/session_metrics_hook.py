#!/usr/bin/env python3
"""
claude-env :: Claude Code SessionStart/SessionEnd cost-tracking hook
File: hooks/session_metrics_hook.py
Purpose:
    Populate metrics_sessions automatically, with zero user configuration.
    SessionStart opens a row (session_id, repo, started_at); SessionEnd sums
    token usage straight out of the session's own transcript and writes the
    final cost + ended_at. Replaces the old dependency on manually-scheduled
    session ingestion, which never wrote cost data in the first place.

Protocol: stdin JSON {session_id, transcript_path, cwd, hook_event_name, ...}
(SessionStart also carries `source`; SessionEnd also carries `reason` — this
hook does not filter on either, it runs the same for every source/reason).
Never blocks anything; never fails loudly.
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


def _repo_slug(cwd: str) -> str | None:
    from lib.repo_policy import repo_slug
    return repo_slug(cwd, fallback_to_basename=True)


def _sum_transcript_usage(transcript_path: str) -> tuple[int, int]:
    """Sum input/output tokens across every assistant message in the
    transcript. Malformed lines, a missing file, or a message with no
    usage field all count as 0 rather than raising."""
    total_in = total_out = 0
    if not transcript_path:
        return 0, 0
    p = Path(transcript_path)
    if not p.exists():
        return 0, 0
    with p.open(errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            msg = rec.get("message") or {}
            if msg.get("role") != "assistant":
                continue
            usage = msg.get("usage") or {}
            total_in += int(usage.get("input_tokens") or 0)
            total_out += int(usage.get("output_tokens") or 0)
    return total_in, total_out


def main() -> int:
    try:
        payload = json.load(sys.stdin)
        event = payload.get("hook_event_name", "")
        session = payload.get("session_id", "hook")
        cwd = payload.get("cwd", "")

        from observability.collectors import start_session, set_usage_totals, end_session

        if event == "SessionStart":
            start_session(session, _repo_slug(cwd))
        elif event == "SessionEnd":
            start_session(session, _repo_slug(cwd))  # ensure a row exists either way
            total_in, total_out = _sum_transcript_usage(payload.get("transcript_path", ""))
            set_usage_totals(session, total_in, total_out)
            end_session(session)
    except Exception:
        pass  # cost tracking must never disturb the session
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
