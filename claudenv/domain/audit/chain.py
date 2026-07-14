"""
claude-env :: Domain - Audit Entities - Hash chain

Groups the shared envelope constants and the hash-chain machinery:
ChainVerificationResult, compute_event_hash, verify_chain, verify_ledger_chain.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from claudenv.domain.value_objects import EventId

if TYPE_CHECKING:
    from claudenv.domain.audit.events import AuditEvent, LedgerRow

GENESIS = "GENESIS"

# Envelope format version. Bump when the canonical-payload shape changes so
# historical rows stay verifiable under the schema they were written with.
ENVELOPE_SCHEMA_VERSION = 2


@dataclass(frozen=True, slots=True)
class ChainVerificationResult:
    """Result of audit chain verification."""
    ok: bool
    broken_at: EventId | None
    total_events: int


def compute_event_hash(prev_hash: str, canonical_json: str) -> str:
    """The one place the chain's hash function is defined — everything that
    writes or verifies the ledger must call this, not reimplement it."""
    return hashlib.sha256((prev_hash + canonical_json).encode("utf-8")).hexdigest()


def verify_chain(events: list["AuditEvent"]) -> ChainVerificationResult:
    """Verify a chain of fully-typed, in-memory events (e.g. freshly created)."""
    prev_hash = GENESIS
    for i, event in enumerate(events):
        if not event.verify(prev_hash):
            return ChainVerificationResult(False, event.event_id, i)
        prev_hash = event.event_hash
    return ChainVerificationResult(True, None, len(events))


def verify_ledger_chain(rows: list["LedgerRow"]) -> ChainVerificationResult:
    """Verify a persisted chain read back from storage (see LedgerRow)."""
    prev_hash = GENESIS
    for i, row in enumerate(rows):
        if not row.verify(prev_hash):
            return ChainVerificationResult(False, row.event_id, i)
        prev_hash = row.event_hash
    return ChainVerificationResult(True, None, len(rows))
