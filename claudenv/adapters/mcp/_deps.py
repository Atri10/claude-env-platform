"""
claude-env :: Adapters - MCP - Shared Dependency Builder

Every MCP server's ``create_server()`` factory needs the same three
dependencies: a :class:`PolicyEngine`, an :class:`IAuditLogger`, and a
:class:`session_id`.  This module provides a single builder so the six
servers don't duplicate the wiring.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.config import get_config
from claudenv.adapters.persistence import SQLiteDatabase
from claudenv.application.policy import PolicyService
from claudenv.domain.policy import PolicyEngine
from claudenv.domain.value_objects import SessionId
from claudenv.ports import IAuditLogger


@dataclass
class McpDeps:
    """Wired dependencies for an MCP server process."""
    repo_root: Path
    audit_logger: IAuditLogger
    policy_engine: PolicyEngine
    session_id: str


def build_deps(
    repo_root: Path | str,
    session_id: str = "mcp-server",
    actor: str = "mcp-server",
) -> McpDeps:
    """Build the common dependency bundle for an MCP server.

    Each MCP server is a separate OS process (launched via stdio); this is
    its own composition root — the DI container is not available because the
    container is process-scoped to the CLI.  That is by design: a Python
    stdio subprocess is cheap to start and dead-simple to wire.
    """
    repo_root = Path(repo_root).resolve()
    config = get_config()

    policy_engine = PolicyService(config).load_engine(str(repo_root))
    db = SQLiteDatabase(config.get_database_dsn())
    audit_logger = SqliteAuditLogger(
        db=db,
        session_id=SessionId.from_string(session_id),
        actor=actor,
        repo=repo_root.name,
        tier=policy_engine.get_compiled().tier,
    )

    return McpDeps(
        repo_root=repo_root,
        audit_logger=audit_logger,
        policy_engine=policy_engine,
        session_id=session_id,
    )
