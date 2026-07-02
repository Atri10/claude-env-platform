"""
claude-env :: local service registry (ports + discovery)
File: lib/services.py
Purpose:
    The platform's only network-listening pieces are local UI servers (approvals
    UI, the observability dashboard/Datasette). Fixed ports (8002/8001) can clash
    with whatever the user is running, so servers pick a free port and record it
    here; other components (and the user, via `claude-env services`) discover the
    actual port instead of guessing.

    This is *transient runtime discovery* — pids and ports, like a lockfile — NOT
    domain persistence. It intentionally does not go through lib/db (the audit DB
    is for auditable events; ports are not). State lives in one JSON file:
        $CLAUDE_ENV_HOME/state/services.json
    Stale entries (dead pid or nothing listening) are pruned on read.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path

_HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
_REGISTRY = _HOME / "state" / "services.json"
_HOST = "127.0.0.1"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _listening(host: str, port: int) -> bool:
    s = socket.socket()
    s.settimeout(0.3)
    try:
        s.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        s.close()


def _alive(pid: int | None) -> bool:
    if not pid:
        return True                      # unknown pid -> judge by the port alone
    try:
        os.kill(pid, 0)                  # signal 0 = liveness probe, doesn't kill
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True                      # exists but not ours
    except OSError:
        return False


def free_port(host: str = _HOST) -> int:
    """Ask the OS for a free ephemeral port (bind :0, read what we got)."""
    s = socket.socket()
    try:
        s.bind((host, 0))
        return s.getsockname()[1]
    finally:
        s.close()


def pick_port(preferred: int, host: str = _HOST) -> int:
    """Return `preferred` if it's bindable, else a free ephemeral port.
    `preferred == 0` means 'always random'. Note: there's an unavoidable tiny
    race between checking and the caller binding — fine for local single-user
    servers, and callers that own the socket (see bind_http) avoid it entirely."""
    if preferred and preferred > 0:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, preferred))
            return preferred
        except OSError:
            pass                          # busy -> fall through to ephemeral
        finally:
            s.close()
    return free_port(host)


def bind_http(handler_cls, preferred: int, host: str = _HOST):
    """Bind a ThreadingHTTPServer race-free: try `preferred`, else let the OS
    assign a free port. Returns (server, actual_port)."""
    from http.server import ThreadingHTTPServer
    if preferred and preferred > 0:
        try:
            srv = ThreadingHTTPServer((host, preferred), handler_cls)
            return srv, preferred
        except OSError:
            pass                          # busy -> ephemeral
    srv = ThreadingHTTPServer((host, 0), handler_cls)
    return srv, srv.server_address[1]


def register(name: str, port: int, host: str = _HOST,
             pid: int | None = None, extra: dict | None = None) -> dict:
    """Record a running service. Overwrites any prior entry for `name`."""
    entry = {"name": name, "host": host, "port": port,
             "url": f"http://{host}:{port}", "pid": pid or os.getpid(),
             "started_at": _now()}
    if extra:
        entry.update(extra)
    data = _read_all()
    data[name] = entry
    _write_all(data)
    return entry


def unregister(name: str) -> None:
    data = _read_all()
    if name in data:
        del data[name]
        _write_all(data)


def get(name: str) -> dict | None:
    """Return a live entry for `name`, or None. Prunes it if stale."""
    return _live(_read_all()).get(name)


def list_live() -> list[dict]:
    """All currently-live services (stale entries pruned from disk)."""
    return sorted(_live(_read_all()).values(), key=lambda e: e.get("name", ""))


# --- internals ---------------------------------------------------------------
def _read_all() -> dict:
    try:
        return json.loads(_REGISTRY.read_text())
    except Exception:
        return {}


def _write_all(data: dict) -> None:
    try:
        _REGISTRY.parent.mkdir(parents=True, exist_ok=True)
        tmp = _REGISTRY.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        os.replace(tmp, _REGISTRY)        # atomic
    except Exception:
        pass                              # discovery is best-effort


def _live(data: dict) -> dict:
    """Filter to live entries (pid alive AND port listening); persist the prune."""
    live = {n: e for n, e in data.items()
            if _alive(e.get("pid")) and _listening(e.get("host", _HOST), e.get("port", 0))}
    if len(live) != len(data):
        _write_all(live)
    return live
