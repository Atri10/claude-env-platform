"""
claude-env :: Application - Dashboard service

Read-only summary over the metrics tables + audit projections. Produces a
structured dict (costs, latency percentiles, retrieval quality, policy
violations, security events, open approvals); rendering is the CLI's job.
Local-only: reads the same SQLite DB, never the network.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from claudenv.domain.observability.percentile import nearest_rank_percentile
from claudenv.domain.observability.window import parse_window_days
from claudenv.ports.observability import IMetricsRepository, IProjectionRepository


class DashboardService:
    """Use-case: assemble the operational summary view."""

    def __init__(self, metrics: IMetricsRepository, projections: IProjectionRepository):
        self._metrics = metrics
        self._projections = projections

    def _since_iso(self, window: str) -> str:
        days = parse_window_days(window)
        since = datetime.now(timezone.utc) - timedelta(days=days)
        return since.strftime("%Y-%m-%dT%H:%M:%S")

    def summary(self, window: str = "30d") -> dict[str, Any]:
        since = self._since_iso(window)

        latency = self._metrics.latency_by_component(since)
        latency_pcts = {
            component: {
                "p50": nearest_rank_percentile(values, 50),
                "p95": nearest_rank_percentile(values, 95),
                "max": max(values) if values else 0.0,
                "count": len(values),
            }
            for component, values in latency.items()
        }

        return {
            "window": window,
            "since": since,
            "top_session_costs": self._metrics.top_session_costs(since),
            "latency": latency_pcts,
            "retrieval_quality": self._metrics.retrieval_quality_by_repo(since),
            "policy_violations": self._projections.recent_policy_violations(),
            "security_events": self._projections.security_event_breakdown(since),
            "open_approvals": self._projections.open_approvals(),
        }
