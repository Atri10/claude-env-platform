"""
claude-env :: Domain - Audit Entities - verify_ledger_chain
"""
from __future__ import annotations

from claudenv.domain.audit._constants import GENESIS
from claudenv.domain.audit.chain_verification_result import ChainVerificationResult
from claudenv.domain.audit.ledger_row import LedgerRow


def verify_ledger_chain(rows: list[LedgerRow]) -> ChainVerificationResult:
    """Verify a persisted chain read back from storage (see LedgerRow)."""
    prev_hash = GENESIS
    for i, row in enumerate(rows):
        if not row.verify(prev_hash):
            return ChainVerificationResult(False, row.event_id, i)
        prev_hash = row.event_hash
    return ChainVerificationResult(True, None, len(rows))
