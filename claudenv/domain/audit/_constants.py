"""
claude-env :: Domain - Audit Entities - shared constants
"""
from __future__ import annotations

GENESIS = "GENESIS"

# Envelope format version. Bump when the canonical-payload shape changes so
# historical rows stay verifiable under the schema they were written with.
ENVELOPE_SCHEMA_VERSION = 2
