"""Incident-mode state — single source of truth for the INCIDENT kill switch.

Incident mode is a filesystem marker at ``$CLAUDE_ENV_HOME/state/INCIDENT``
(JSON). Every enforcement surface (native-tool hooks, the policy engine, the
filesystem MCP server, the RAG indexer) must consult it so that activating
incident mode fails *everything* closed. Centralising the read/write here keeps
the semantics consistent and stops each surface re-deriving the path.

This mirrors the resolution already used by ``claudenv/cli.py``'s ``incident``
command and ``claudenv/adapters/hooks/policy_hook.py``.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import logging
logger = logging.getLogger(__name__)


def claude_env_home() -> Path:
    """Resolve ``$CLAUDE_ENV_HOME`` (defaults to ``~/.claude-env``)."""
    return Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))


def incident_marker_path(home: Path | None = None) -> Path:
    return (home or claude_env_home()) / "state" / "INCIDENT"


def is_incident_active(home: Path | None = None) -> bool:
    """True when the INCIDENT marker exists — operations must be denied."""
    return incident_marker_path(home).exists()


@dataclass
class IncidentState:
    active: bool
    reason: str = ""
    by: str = ""
    since: str = ""

    @classmethod
    def read(cls, home: Path | None = None) -> "IncidentState":
        marker = incident_marker_path(home)
        if not marker.exists():
            return cls(active=False)
        try:
            data = json.loads(marker.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            # A present-but-unreadable marker still means "deny everything".
            logger.warning("incident marker unreadable; treating as active (deny)", exc_info=True)
            return cls(active=True)
        return cls(
            active=True,
            reason=data.get("reason", ""),
            by=data.get("by", ""),
            since=data.get("since", ""),
        )


def write_incident(reason: str, by: str, home: Path | None = None) -> IncidentState:
    marker = incident_marker_path(home)
    marker.parent.mkdir(parents=True, exist_ok=True)
    import datetime as _dt

    since = _dt.datetime.now(_dt.UTC).isoformat()
    marker.write_text(json.dumps({"reason": reason, "by": by, "since": since}, indent=2))
    return IncidentState(active=True, reason=reason, by=by, since=since)


def clear_incident(home: Path | None = None) -> None:
    marker = incident_marker_path(home)
    if marker.exists():
        marker.unlink()
