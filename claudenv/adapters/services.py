"""
claude-env :: Adapters - File-backed Service Registry

Local UI servers (approvals UI, dashboard) pick a free port and record it
here so other components/processes discover the actual port instead of
guessing or hardcoding one. This is deliberately cross-process: the terminal
MCP server, the approvals UI subprocess, and the `claude-env services` CLI
are three separate OS processes, so an in-memory registry (a plain dict
inside one process's DI container) is invisible to the other two and cannot
serve as a real registry. State lives in one JSON file:
    $CLAUDE_ENV_HOME/state/services.json
Stale entries (dead pid or nothing listening) are pruned on read.
"""
from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from claudenv.ports.services import IServiceRegistry

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
        return True  # unknown pid -> judge by the port alone
    try:
        os.kill(pid, 0)  # signal 0 = liveness probe, doesn't kill
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but not ours
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
    `preferred == 0` means 'always random'. There's an unavoidable tiny race
    between checking and the caller binding — fine for local single-user
    servers; callers that own the socket (see bind_http) avoid it entirely."""
    if preferred and preferred > 0:
        s = socket.socket()
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind((host, preferred))
            return preferred
        except OSError:
            pass  # busy -> fall through to ephemeral
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
            pass  # busy -> ephemeral
    srv = ThreadingHTTPServer((host, 0), handler_cls)
    return srv, srv.server_address[1]


class FileServiceRegistry(IServiceRegistry):
    """Cross-process service registry backed by a single JSON file."""

    def __init__(self, claude_env_home: str | Path):
        self._registry_path = Path(claude_env_home) / "state" / "services.json"

    def register(
            self,
            name: str,
            port: int,
            pid: int | None = None,
            extra: dict | None = None,
    ) -> dict[str, Any]:
        entry = {
            "name": name, "host": _HOST, "port": port,
            "url": f"http://{_HOST}:{port}", "pid": pid or os.getpid(),
            "started_at": _now(),
        }
        if extra:
            entry.update(extra)
        data = self._read_all()
        data[name] = entry
        self._write_all(data)
        return entry

    def unregister(self, name: str) -> None:
        data = self._read_all()
        if name in data:
            del data[name]
            self._write_all(data)

    def get(self, name: str) -> dict[str, Any] | None:
        return self._live(self._read_all()).get(name)

    def list_live(self) -> list[dict[str, Any]]:
        return sorted(self._live(self._read_all()).values(), key=lambda e: e.get("name", ""))

    def _read_all(self) -> dict:
        try:
            return json.loads(self._registry_path.read_text())
        except Exception:
            return {}

    def _write_all(self, data: dict) -> None:
        try:
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._registry_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(data, indent=2))
            os.replace(tmp, self._registry_path)  # atomic
        except Exception:
            pass  # discovery is best-effort

    def _live(self, data: dict) -> dict:
        """Filter to live entries (pid alive AND port listening); persist the prune."""
        live = {
            n: e for n, e in data.items()
            if _alive(e.get("pid")) and _listening(e.get("host", _HOST), e.get("port", 0))
        }
        if len(live) != len(data):
            self._write_all(live)
        return live
