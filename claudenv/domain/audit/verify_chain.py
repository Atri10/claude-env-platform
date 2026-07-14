"""
claude-env :: Domain - Audit Entities - verify_chain
"""
from __future__ import annotations

from claudenv.domain.audit._constants import GENESIS
from claudenv.domain.audit.audit_event import AuditEvent
from claudenv.domain.audit.chain_verification_result import ChainVerificationResult


def verify_chain(events: list[AuditEvent]) -> ChainVerificationResult:
    """Verify a chain of fully-typed, in-memory events (e.g. freshly created)."""
    prev_hash = GENESIS
    for i, event in enumerate(events):
        if not event.verify(prev_hash):
            return ChainVerificationResult(False, event.event_id, i)
        prev_hash = event.event_hash
    return ChainVerificationResult(True, None, len(events))
