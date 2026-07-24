"""
claude-env :: Domain - Incident State (pure)

Incident-mode state — the INCIDENT kill-switch. This module holds ONLY the
pure ``IncidentState`` dataclass and a pure boolean helper. The filesystem
I/O (marker read/write) lives behind the ``IIncidentStore`` port
(``claudenv/ports/incident.py``) implemented by ``FileIncidentStore``
(``claudenv/adapters/incident.py``).

Every enforcement surface (native-tool hooks, the policy engine, the
filesystem MCP server, the RAG indexer) consults the boolean so that
activating incident mode fails *everything* closed.

Backward-compat: ``is_incident_active`` / ``write_incident`` /
``clear_incident`` are re-exported here from ``claudenv.adapters.incident``
so legacy call sites (cli.py, tests) keep importing from
``claudenv.domain.incident``. New code should inject ``IIncidentStore``.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class IncidentState:
    """Immutable snapshot of the incident marker's content."""
    active: bool
    reason: str = ""
    by: str = ""
    since: str = ""

    @classmethod
    def read(cls, home=None) -> "IncidentState":
        """Backward-compat reader delegating to the default FileIncidentStore."""
        from claudenv.adapters.incident import FileIncidentStore
        return FileIncidentStore(home).read()


def is_incident_active(active: bool) -> bool:
    """Pure domain helper: pass through the boolean read by the adapter.

    Kept as a named function (not just ``bool``) so call sites read as
    intent (``if is_incident_active(store.is_active()):``) rather than a
    bare-truthiness test, and so the domain has a single named entry point
    for the kill-switch predicate.
    """
    return active


# --- Backward-compat re-exports (delegate to adapters, lazily) ---
# These keep ``from claudenv.domain.incident import write_incident`` working
# for cli.py and tests without pulling adapters eagerly at domain-import
# time (avoids a circular import). They are thin wrappers.

def is_incident_active_compat(home=None) -> bool:  # noqa: F811 - intentional alias
    """Backward-compat: read the incident marker from the filesystem."""
    from claudenv.adapters.incident import FileIncidentStore
    return FileIncidentStore(home).is_active()


def write_incident(reason: str, by: str, home=None) -> IncidentState:
    """Backward-compat: arm incident mode via the default FileIncidentStore."""
    from claudenv.adapters.incident import FileIncidentStore
    return FileIncidentStore(home).write(reason, by)


def clear_incident(home=None) -> None:
    """Backward-compat: disarm incident mode via the default FileIncidentStore."""
    from claudenv.adapters.incident import FileIncidentStore
    FileIncidentStore(home).clear()
