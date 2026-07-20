"""
claude-env :: Adapters - Audit Hook

PostToolUse hook for recording tool outcomes to audit ledger.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from claudenv.adapters.config import get_config
from claudenv.domain.value_objects import RepoSlug, SessionId, Tier
from claudenv.ports import IAuditLogger
from claudenv.ports.hooks.interfaces import IPostToolUseHook
from claudenv.logging_config import configure_logging
logger = logging.getLogger(__name__)


class AuditHook(IPostToolUseHook):
    """Records tool outcomes to audit log."""
    # Install metadata (consumed by HookInstaller).
    HOOK_MATCHER = "Read|Write|Edit|NotebookEdit|Glob|Grep|Bash"
    HOOK_TIMEOUT = 5


    def __init__(
            self,
            audit_logger: IAuditLogger,
            repo_root: Path,
    ):
        self.audit = audit_logger
        self.repo_root = repo_root
        self._sinks: list[Any] = []

    def add_sink(self, sink: Any) -> None:
        """Add external audit event sink."""
        self._sinks.append(sink)

    def on_tool_complete(self, tool_outcome: dict[str, Any]) -> None:
        tool_name = tool_outcome.get("tool_name", "unknown")
        success = tool_outcome.get("success", False)
        details = tool_outcome.get("details", {})

        self.audit.tool_call(
            tool=tool_name,
            args=details,
            result_kind="ok" if success else "error",
            duration_ms=tool_outcome.get("duration_ms"),
        )

        # Flush to sinks
        for sink in self._sinks:
            if hasattr(sink, "write"):
                sink.write(json.dumps({
                    "tool": tool_name,
                    "success": success,
                    "details": details,
                }).encode() + b"\n")

    def record_decision(self, approval: dict[str, Any]) -> None:
        """Record approval/denial decision."""
        self.audit.agent_action(
            agent="approval_gate",
            action="approve" if approval.get("approved") else "deny",
            target=approval.get("resource", ""),
            summary=approval.get("reason", ""),
        )

    def flush(self) -> None:
        for sink in self._sinks:
            if hasattr(sink, "flush"):
                sink.flush()
        self.audit.flush()


def create_hook(
        repo_root: Path | str,
        session_id: str = "audit-hook",
        actor: str = "audit-hook",
) -> AuditHook:
    repo_root = Path(repo_root).resolve()
    config = get_config()

    audit_logger = config.get_audit_logger(
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=RepoSlug.from_string(repo_root.name),
        tier=Tier.INTERNAL,
    )

    return AuditHook(audit_logger, repo_root)


def main() -> None:
    configure_logging(console=False)
    logger.debug("audit hook invoked")
    """CLI entry point."""
    try:
        hook_input = json.load(sys.stdin)
        tool_outcome = hook_input.get("tool_outcome", {})

        repo_root = Path(os.environ.get("CLAUDE_ENV_REPO_ROOT", os.getcwd())).resolve()
        hook = create_hook(repo_root)
        hook.on_tool_complete(tool_outcome)

        json.dump({}, sys.stdout)
    except Exception:
        logger.exception("audit hook failed; emitted empty ack")
        json.dump({}, sys.stdout)


if __name__ == "__main__":
    main()
