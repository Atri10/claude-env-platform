"""Coverage for the local service registry / port picker (lib/services.py).
Run: pytest tests/ -q"""
import socket
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lib.services as svc


def test_free_port_is_bindable():
    p = svc.free_port()
    s = socket.socket()
    s.bind(("127.0.0.1", p))          # the returned port is actually free
    s.close()


def test_pick_port_returns_free_preferred():
    free = svc.free_port()
    assert svc.pick_port(free) == free


def test_pick_port_falls_back_when_busy():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    busy = s.getsockname()[1]
    s.listen()
    try:
        got = svc.pick_port(busy)
        assert got != busy            # busy preferred -> a different free port
        assert got > 0
    finally:
        s.close()


def test_register_get_list_prune(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_REGISTRY", tmp_path / "services.json")
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.listen()
    try:
        svc.register("approvals", port)
        got = svc.get("approvals")
        assert got and got["port"] == port and got["url"].endswith(str(port))
        assert [e["name"] for e in svc.list_live()] == ["approvals"]
    finally:
        s.close()
    # socket closed -> nothing listening -> entry is stale and pruned
    assert svc.get("approvals") is None
    assert svc.list_live() == []
    assert not __import__("json").loads((tmp_path / "services.json").read_text())
