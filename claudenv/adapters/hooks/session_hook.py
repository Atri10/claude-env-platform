"""
claude-env :: Adapters - Session Hook (PostToolUse)

Session lifecycle management: context loading, checkpoint restore, context save.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

from claudenv.application.memory import MemoryService
from claudenv.ports.hooks.interfaces import IPostToolUseHook


class SessionHook(IPostToolUseHook):
    """Session lifecycle hook for memory context management."""

    def __init__(
            self,
            memory_service: MemoryService,
            audit_logger,
            session_id: str = "session",
    ):
        self.memory = memory_service
        self.audit = audit_logger
        self.session_id = session_id
        self._context: Dict[str, Any] = {}

    def on_tool_complete(self, tool_outcome: Dict[str, Any]) -> None:
        """Track tool outcomes for session context."""
        pass

    def on_session_start(self, session_id: str) -> Dict[str, Any]:
        """Load memory context at session start."""
        graph = self.memory.create_graph(f"session:{session_id}", isolated=True)
        self._context = {
            "session_id": session_id,
            "graph": graph,
            "nodes_loaded": len(graph.list_nodes(limit=1000)),
        }
        return self._context

    def on_session_end(self, session_id: str) -> None:
        """Persist session context at end."""
        self.audit.agent_action(
            agent="session_hook",
            action="end",
            target=session_id,
            summary=f"nodes={self._context.get('nodes_loaded', 0)}",
        )
        self._context.clear()

    def restore_checkpoint(self, session_id: str, checkpoint_id: str) -> Dict[str, Any]:
        """Restore session from checkpoint."""
        graph = self.memory.create_graph(f"session:{session_id}:checkpoint:{checkpoint_id}", isolated=True)
        self._context = {
            "session_id": session_id,
            "checkpoint_id": checkpoint_id,
            "graph": graph,
        }
        return self._context


def create_hook(
        session_id: str = "session",
        actor: str = "session_hook",
) -> SessionHook:
    """Factory for SessionHook."""
    from claudenv.adapters.config import get_config
    from claudenv.domain.value_objects import SessionId, Tier

    config = get_config()
    memory_service = config.get_memory_service()

    audit_logger = config.get_audit_logger(
        session_id=SessionId.from_string("session"),
        actor=actor,
        repo=None,
        tier=Tier.INTERNAL,
    )

    return SessionHook(memory_service, audit_logger, "session")


def main() -> None:
    """CLI entry point for PostToolUse hook."""
    import sys
    import json

    try:
        hook_input = json.load(sys.stdin)
        tool_outcome = hook_input.get("tool_outcome", {})

        hook = create_hook()
        hook.on_tool_complete(tool_outcome)

        json.dump({}, sys.stdout)
    except Exception:
        json.dump({}, sys.stdout)


if __name__ == "__main__":
    main()
