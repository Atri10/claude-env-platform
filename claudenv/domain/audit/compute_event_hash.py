"""
claude-env :: Domain - Audit Entities - compute_event_hash
"""
from __future__ import annotations

import hashlib


def compute_event_hash(prev_hash: str, canonical_json: str) -> str:
    """The one place the chain's hash function is defined — everything that
    writes or verifies the ledger must call this, not reimplement it."""
    return hashlib.sha256((prev_hash + canonical_json).encode("utf-8")).hexdigest()
