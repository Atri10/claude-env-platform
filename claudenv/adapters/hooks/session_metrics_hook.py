"""
claude-env :: Adapters - Session Cost Tracking Hook (SessionStart/SessionEnd)

Records session token usage automatically at session boundaries so
``claude-env budget`` and the dashboard populate with zero user setup.

Dispatches on ``payload["hook_event_name"]`` (matcher "*"), following the
same never-block / never-raise shape as audit_hook.py: the whole body is
wrapped so a failure can never disrupt the user's session.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from claudenv.adapters.config import get_config
from claudenv.adapters.logging import configure_logging
from claudenv.ports.observability import ISessionMetricsRepository

logger = logging.getLogger(__name__)



class SessionMetricsHook:
    """Claude Code SessionStart/SessionEnd cost-tracking hook."""

    # Install metadata (consumed by HookInstaller).
    HOOK_MATCHER = "*"
    HOOK_TIMEOUT = 5

    @staticmethod
    def _resolve_repo(cwd: str) -> str | None:
        """Resolve the repo slug for ``cwd``.

        Honors ``.claude/repo-policy.yaml``'s ``repo:`` field, falling back to
        the directory basename when no policy is present (mirrors the legacy
        ``repo_slug(cwd, fallback_to_basename=True)``).
        """
        policy = Path(cwd) / ".claude" / "repo-policy.yaml"
        if policy.exists():
            try:
                data = yaml.safe_load(policy.read_text()) or {}
                repo = data.get("repo")
                if repo:
                    return str(repo)
            except Exception:
                logger.warning("could not resolve repo from cwd", exc_info=True)
                pass
        return Path(cwd).resolve().name

    @staticmethod
    def _sum_transcript(transcript_path: str) -> tuple[int, int]:
        """Sum assistant-message token usage across a transcript JSONL.

        Malformed lines and messages without a ``usage`` field are skipped; a
        missing file yields (0, 0). Returns (input_tokens, output_tokens).
        """
        total_in = total_out = 0
        path = Path(transcript_path)
        if not path.exists():
            return 0, 0
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                logger.warning("skipping unparsable transcript line", exc_info=True)
                continue
            message = record.get("message", {})
            if message.get("role") != "assistant":
                continue
            usage = message.get("usage") or {}
            total_in += int(usage.get("input_tokens", 0) or 0)
            total_out += int(usage.get("output_tokens", 0) or 0)
        return total_in, total_out

    @staticmethod
    def run(
        payload: dict[str, Any],
        metrics: ISessionMetricsRepository | None = None,
    ) -> int:
        """Process one SessionStart/SessionEnd payload. Returns 0 always."""
        try:
            event = payload.get("hook_event_name")
            session_id = payload.get("session_id")
            if not session_id:
                return 0
            if metrics is None:
                metrics = get_config().get_session_metrics_repository()
            cwd = payload.get("cwd", "")
            repo = SessionMetricsHook._resolve_repo(cwd) if cwd else None
            if event == "SessionStart":
                metrics.start_session(session_id, repo)
            elif event == "SessionEnd":
                # Ensure the row exists even if SessionStart never fired
                # (e.g. session predates the hook install). INSERT OR IGNORE
                # leaves an existing row's started_at untouched.
                metrics.start_session(session_id, repo)
                total_in, total_out = SessionMetricsHook._sum_transcript(
                    payload.get("transcript_path", "")
                )
                metrics.set_usage_totals(session_id, total_in, total_out)
                metrics.end_session(session_id)
        except Exception:
            logger.exception("session metrics hook failed; continuing")
        return 0

    @staticmethod
    def main() -> None:
        """CLI entry point for the SessionStart/SessionEnd hook."""
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = {}
        sys.exit(SessionMetricsHook.run(payload))


def run(payload: dict[str, Any], metrics: "ISessionMetricsRepository | None" = None) -> int:
    """Module-level alias for :meth:`SessionMetricsHook.run`."""
    return SessionMetricsHook.run(payload, metrics)


def main() -> None:
    """Module entry point: ``python -m claudenv.adapters.hooks.session_metrics_hook``."""
    configure_logging(console=False)
    SessionMetricsHook.main()


if __name__ == "__main__":
    main()
