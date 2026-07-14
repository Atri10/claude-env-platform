"""
claude-env :: Ports - Tool-scoped audit logger interface
"""
from __future__ import annotations

from typing import Protocol

from claudenv.ports.audit.audit_logger import IAuditLogger


class IToolAuditLogger(IAuditLogger, Protocol):
    """Audit logger scoped to native tool calls (Read/Write/Edit/Bash)."""
