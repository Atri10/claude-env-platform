"""
claude-env :: Domain - Query hash (pure)

Truncated sha256 used only to group repeated identical queries in
rag_chunk_feedback. Not a security control.
"""
from __future__ import annotations

import hashlib


def query_hash(query: str) -> str:
    """sha256(query)[:16] — a grouping key, not a cryptographic commitment."""
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
