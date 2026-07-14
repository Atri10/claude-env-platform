"""
claude-env :: Domain - Audit Entities - LedgerRow
"""
from __future__ import annotations

from dataclasses import dataclass

from claudenv.domain.value_objects import EventId
from claudenv.domain.audit.compute_event_hash import compute_event_hash


@dataclass(frozen=True, slots=True)
class LedgerRow:
    """A persisted ledger row, verified directly against its own stored
    canonical envelope. Deliberately avoids reconstructing a typed AuditEvent
    from separate columns: that reconstruction previously drifted from what
    was actually hashed at write time (dropped fields, regenerated
    session/request ids, mismatched payload shape), which silently made
    chain verification fail even for an untampered ledger. Storing and
    replaying the exact canonical string closes that gap for good.
    """
    event_id: EventId
    canonical_json: str
    prev_hash: str
    event_hash: str

    def verify(self, expected_prev_hash: str) -> bool:
        return (
            self.prev_hash == expected_prev_hash
            and compute_event_hash(self.prev_hash, self.canonical_json) == self.event_hash
        )
