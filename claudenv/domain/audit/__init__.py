"""
claude-env :: Domain - Audit Entities
"""
from __future__ import annotations

from claudenv.domain.audit.chain import (
    ENVELOPE_SCHEMA_VERSION,
    GENESIS,
    ChainVerificationResult,
    compute_event_hash,
    verify_chain,
    verify_ledger_chain,
)
from claudenv.domain.audit.events import AuditEvent, EventType, LedgerRow
from claudenv.domain.audit.projections import (
    AgentActionProjection,
    HumanApprovalProjection,
    PolicyViolationProjection,
    ProjectionBuilder,
    RetrievalProjection,
    SecurityProjection,
    ToolCallProjection,
)

# Re-exported so `from claudenv.domain.audit import X` keeps working exactly
# as it did when audit.py imported these from value_objects directly.
from claudenv.domain.value_objects import (
    EventId,
    RepoSlug,
    RequestId,
    SessionId,
    Tier,
    iso_now,
)

__all__ = [
    "EventId",
    "RequestId",
    "RepoSlug",
    "SessionId",
    "Tier",
    "iso_now",
    "GENESIS",
    "ENVELOPE_SCHEMA_VERSION",
    "EventType",
    "AuditEvent",
    "ToolCallProjection",
    "AgentActionProjection",
    "RetrievalProjection",
    "SecurityProjection",
    "PolicyViolationProjection",
    "HumanApprovalProjection",
    "ProjectionBuilder",
    "ChainVerificationResult",
    "verify_chain",
    "compute_event_hash",
    "LedgerRow",
    "verify_ledger_chain",
]
