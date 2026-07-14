"""
claude-env :: Application - Feedback service

Retrieval-quality feedback loop. Records which chunks were 'retrieved' and turns
historical 'used' signals into a per-chunk reranking boost the retrieve pipeline
adds after reranking. Gated by CLAUDE_ENV_FEEDBACK_BOOST (only the exact token
'false', case-insensitive, disables boosting).
"""
from __future__ import annotations

import os
from typing import Any

from claudenv.domain.observability.boost_calculator import usage_boosts_from_counts
from claudenv.domain.observability.query_hash import query_hash
from claudenv.ports.observability import IFeedbackRepository


def boost_enabled() -> bool:
    """Boosting is on unless CLAUDE_ENV_FEEDBACK_BOOST is the token 'false'."""
    return os.environ.get("CLAUDE_ENV_FEEDBACK_BOOST", "true").lower() != "false"


class FeedbackService:
    """Use-case: record retrieval events and compute usage boosts."""

    def __init__(self, feedback: IFeedbackRepository):
        self._feedback = feedback

    def record_retrieved(
        self,
        repo: str,
        branch: str,
        query: str,
        chunks: list[dict[str, Any]],
        session_id: str,
    ) -> None:
        """Log one 'retrieved' row per returned chunk. Never raises."""
        self._feedback.record_retrieved(
            repo=repo,
            branch=branch,
            query_hash=query_hash(query),
            chunks=chunks,
            session_id=session_id,
        )

    def usage_boosts(self, repo: str, branch: str) -> dict[str, float]:
        """chunk_id -> boost from historical 'used' signals. Never raises.

        Returns an empty map when boosting is disabled.
        """
        if not boost_enabled():
            return {}
        return usage_boosts_from_counts(self._feedback.used_counts(repo, branch))

    def stats(self, repo: str | None = None) -> dict[str, Any]:
        """Aggregate feedback stats for the dashboard / CLI. May raise on DB error."""
        totals = self._feedback.signal_totals(repo)
        out: dict[str, Any] = dict(totals)
        out["top_used_files"] = self._feedback.top_used_files(repo)
        return out
