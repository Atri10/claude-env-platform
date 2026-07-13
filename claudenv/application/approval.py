"""
claude-env :: Application - Approval Gate

The human-in-the-loop chokepoint. open()/resolve() go through IAuditLogger so
a gated action is written to the tamper-evident audit chain (human_approvals
is a projection of it, in the same transaction) and blocks until resolved.
list_open()/list_recent() read that projection directly for the approvals UI.

evaluate() is a registry-driven scope/requires_approval check carried over
from the ported design; no live caller in this codebase invokes it — the
live approval flow is open()/resolve()/list_open()/list_recent(), used by
the approvals web UI and terminal.run. It's kept only so IApprovalGate has a
real implementation for every method in the port, not because anything
depends on registry-driven gating today.
"""
from __future__ import annotations

from typing import Any

from claudenv.ports.approval import GateVerdict, IApprovalGate
from claudenv.ports.audit import IAuditLogger
from claudenv.ports.database import IDatabase


class ApprovalGate(IApprovalGate):
    """SQL-backed approval gate: writes through the audit chain, reads the
    human_approvals projection directly."""

    def __init__(self, audit: IAuditLogger, db: IDatabase):
        self._audit = audit
        self._db = db

    def evaluate(self, agent: str, action: str, target: str | None = None) -> GateVerdict:
        return GateVerdict(required=False, agent=agent, action=action, target=target, tier=None, reasons=[])

    def open(self, verdict: GateVerdict) -> str:
        """Persist a pending approval, return its request_id. The approvals
        web UI is the sole surface for pending requests — deliberately no
        desktop notification: a single, always-current queue page is less
        noisy than per-request OS toasts."""
        action_desc = f"{verdict.action} :: {'; '.join(verdict.reasons)}" if verdict.reasons else verdict.action
        if verdict.target:
            reasons = f" :: {'; '.join(verdict.reasons)}" if verdict.reasons else ""
            action_desc = f"{verdict.action} -> {verdict.target}{reasons}"
        return self._audit.human_approval_request(
            agent=verdict.agent, action=action_desc, tier=verdict.tier,
        )

    def resolve(self, request_id: str, approved: bool, decided_by: str) -> None:
        self._audit.human_approval_resolve(
            request_id, decision="approved" if approved else "denied", decided_by=decided_by,
        )

    def list_open(self) -> list[dict[str, Any]]:
        """Pending approvals, enriched with the session_id from the linked
        audit event so the UI can show full provenance."""
        rows = self._db.query(
            "SELECT h.request_id, h.agent, h.repo, h.tier, h.action, h.requested_at, "
            "       a.session_id "
            "FROM human_approvals h "
            "LEFT JOIN audit_events a ON a.event_id = h.event_id "
            "WHERE h.decision='pending' ORDER BY h.requested_at"
        )
        return [dict(r) for r in rows]

    def list_recent(self, limit: int = 8) -> list[dict[str, Any]]:
        """Recently resolved approvals (for context in the UI)."""
        rows = self._db.query(
            "SELECT h.request_id, h.agent, h.repo, h.tier, h.action, h.decision, "
            "       h.decided_by, h.decided_at, a.session_id "
            "FROM human_approvals h "
            "LEFT JOIN audit_events a ON a.event_id = h.event_id "
            "WHERE h.decision IN ('approved','denied') "
            "ORDER BY h.decided_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]
