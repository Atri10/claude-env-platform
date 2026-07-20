"""
claude-env :: Adapters - Session cost collectors

A collector turns an external signal (a terminal command execution, a hook
event, ...) into a ``metrics_sessions`` row via ``ISessionMetricsRepository``
so the budget and dashboard surfaces populate with zero manual setup.

The terminal MCP server uses ``SessionCostCollector`` to attribute each
command it executes to its MCP session (``CLAUDE_ENV_SESSION``), keeping
terminal activity visible alongside LLM spend.
"""
from __future__ import annotations

from claudenv.ports.observability import ISessionMetricsRepository


def estimate_tokens(text: str) -> int:
    """Coarse token proxy: ~4 chars per token (typical for English/code).

    Terminal commands are not LLM calls, so there is no real token usage to
    record. We approximate from the command/response text so the recorded cost
    is deterministic and the budget/dashboard math stays exercised end to end.
    """
    return max(0, len(text or "") // 4)


class SessionCostCollector:
    """Record a session's terminal-execution cost into ``metrics_sessions``."""

    def __init__(self, metrics: ISessionMetricsRepository):
        self._metrics = metrics

    def record(
        self,
        session_id: str,
        repo: str | None,
        input_tokens: int,
        output_tokens: int,
        model: str = "default",
    ) -> None:
        """Open (or reuse) the session row and overwrite its cost totals.

        Mirrors the documented ``set_usage_totals`` contract: a re-fire
        overwrites, it does not increment.
        """
        self._metrics.start_session(session_id, repo)
        self._metrics.set_usage_totals(session_id, input_tokens, output_tokens, model)
        self._metrics.end_session(session_id)

    def record_command(self, session_id: str, repo: str | None, command: str, output: str) -> None:
        """Estimate tokens from ``command`` + ``output`` and record the cost."""
        self.record(
            session_id,
            repo,
            estimate_tokens(command),
            estimate_tokens(output),
        )
