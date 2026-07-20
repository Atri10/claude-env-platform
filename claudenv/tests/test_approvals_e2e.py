"""
End-to-end approvals flow.

The terminal server opens a ``human_approvals`` row and blocks; the operator
resolves it through the approvals web UI (which records the approver as
``user@host``). These tests exercise the real ``ApprovalGate`` decision path
and the approvals UI HTTP flow in-process — no real browser, no real network
service — by POSTing to the actual ``make_handler`` server on localhost.
"""
from __future__ import annotations

import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

from claudenv._data import sql_dir as _sql_dir
from claudenv.adapters.approvals_ui import DECIDED_BY, TOKEN, make_handler
from claudenv.adapters.audit import SqliteAuditLogger
from claudenv.adapters.persistence import SQLiteDatabase
from claudenv.application.approval import ApprovalGate
from claudenv.domain.value_objects import SessionId, Tier
from claudenv.ports.approval import GateVerdict


def _gate(home: Path) -> tuple[ApprovalGate, SQLiteDatabase]:
    (home / "state").mkdir(parents=True, exist_ok=True)
    dsn = f"sqlite:///{home}/state/claude-env.db"
    db = SQLiteDatabase(dsn)
    for f in sorted(_sql_dir().glob("*.sql")):
        db._conn.executescript(f.read_text())
    audit = SqliteAuditLogger(
        db, SessionId.from_string("e2e"), actor="e2e", repo="repo", tier=Tier.INTERNAL,
    )
    return ApprovalGate(audit, db), db


def _verdict() -> GateVerdict:
    return GateVerdict(
        required=True, agent="terminal", action="terminal.run: reboot",
        target=None, tier=Tier.SENSITIVE, reasons=["state-changing"],
    )


class TestApprovalGate:
    def test_open_records_pending(self, tmp_path):
        gate, db = _gate(tmp_path / "h")
        rid = gate.open(_verdict())
        open_rows = gate.list_open()
        assert len(open_rows) == 1
        assert open_rows[0]["request_id"] == rid
        db.close()

    def test_resolve_records_user_host(self, tmp_path):
        gate, db = _gate(tmp_path / "h")
        rid = gate.open(_verdict())
        gate.resolve(rid, approved=True, decided_by="alice@host")
        recent = gate.list_recent(limit=1)
        assert recent[0]["decision"] == "approved"
        assert recent[0]["decided_by"] == "alice@host"
        assert gate.list_open() == []
        db.close()


class TestApprovalsUIInProcess:
    def _start(self, gate):
        handler_cls = make_handler(gate)
        httpd = HTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        return httpd, port

    def test_ui_approve_flow(self, tmp_path):
        gate, db = _gate(tmp_path / "h")
        rid = gate.open(_verdict())
        httpd, port = self._start(gate)
        try:
            # Open request is visible in the JSON API.
            data = urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/approvals", timeout=5,
            ).read()
            assert rid.encode() in data

            # Approve through the real form POST (with the CSRF token).
            body = urllib.parse.urlencode(
                {"id": rid, "token": TOKEN, "decision": "approve"},
            ).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/resolve", data=body, method="POST",
            )
            urllib.request.urlopen(req, timeout=5)

            recent = gate.list_recent(limit=1)
            assert recent[0]["decision"] == "approved"
            # Approver is recorded as OS user@host, never asked for a name.
            assert "@" in DECIDED_BY
            assert recent[0]["decided_by"] == DECIDED_BY
        finally:
            httpd.shutdown()
        db.close()

    def test_ui_bad_token_rejected(self, tmp_path):
        gate, db = _gate(tmp_path / "h")
        rid = gate.open(_verdict())
        httpd, port = self._start(gate)
        try:
            body = urllib.parse.urlencode(
                {"id": rid, "token": "wrong", "decision": "approve"},
            ).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/resolve", data=body, method="POST",
            )
            with pytest.raises(urllib.error.HTTPError) as exc:
                urllib.request.urlopen(req, timeout=5)
            assert exc.value.code == 403
            # Still pending — the bad token did not resolve it.
            assert gate.list_open()[0]["request_id"] == rid
        finally:
            httpd.shutdown()
        db.close()
