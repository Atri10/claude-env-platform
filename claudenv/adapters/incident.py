"""
claude-env :: Adapters - Filesystem Incident Store

Filesystem-backed implementation of ``IIncidentStore``. The incident marker
is a JSON file at ``$CLAUDE_ENV_HOME/state/INCIDENT``. Every enforcement
surface reads it through this adapter (or the port) so the domain layer
stays pure.
"""
from __future__ import annotations

import datetime as _dt
import json
import logging
import os
from pathlib import Path

from claudenv.domain.incident import IncidentState
from claudenv.ports.incident import IIncidentStore

logger = logging.getLogger(__name__)


def claude_env_home() -> Path:
    """Resolve ``$CLAUDE_ENV_HOME`` (defaults to ``~/.claude-env``)."""
    return Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))


def incident_marker_path(home: Path | None = None) -> Path:
    return (home or claude_env_home()) / "state" / "INCIDENT"


class FileIncidentStore(IIncidentStore):
    """Filesystem-backed incident marker store."""

    def __init__(self, home: Path | None = None):
        self._home = home

    def _marker(self) -> Path:
        return incident_marker_path(self._home)

    def is_active(self) -> bool:
        return self._marker().exists()

    def read(self) -> IncidentState:
        marker = self._marker()
        if not marker.exists():
            return IncidentState(active=False)
        try:
            data = json.loads(marker.read_text() or "{}")
        except (json.JSONDecodeError, OSError):
            # A present-but-unreadable marker still means "deny everything".
            logger.warning("incident marker unreadable; treating as active (deny)", exc_info=True)
            return IncidentState(active=True)
        return IncidentState(
            active=True,
            reason=data.get("reason", ""),
            by=data.get("by", ""),
            since=data.get("since", ""),
        )

    def write(self, reason: str, by: str) -> IncidentState:
        marker = self._marker()
        marker.parent.mkdir(parents=True, exist_ok=True)
        since = _dt.datetime.now(_dt.UTC).isoformat()
        marker.write_text(json.dumps({"reason": reason, "by": by, "since": since}, indent=2))
        return IncidentState(active=True, reason=reason, by=by, since=since)

    def clear(self) -> None:
        marker = self._marker()
        if marker.exists():
            marker.unlink()


# --- Backward-compatible module-level functions ---
# These keep the old call sites (cli.py, tests) working while the migration
# to constructor-injected IIncidentStore progresses. They delegate to a
# default FileIncidentStore instance.

_default_store: FileIncidentStore | None = None


def _store(home: Path | None = None) -> FileIncidentStore:
    global _default_store
    if home is not None:
        return FileIncidentStore(home)
    if _default_store is None:
        _default_store = FileIncidentStore()
    return _default_store


def is_incident_active(home: Path | None = None) -> bool:
    """True when the INCIDENT marker exists — operations must be denied."""
    return _store(home).is_active()


def write_incident(reason: str, by: str, home: Path | None = None) -> IncidentState:
    return _store(home).write(reason, by)


def clear_incident(home: Path | None = None) -> None:
    _store(home).clear()


def IncidentState_read(home: Path | None = None) -> IncidentState:
    """Backward-compatible reader (old call sites used ``IncidentState.read(home)``)."""
    return _store(home).read()
