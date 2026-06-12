#!/usr/bin/env python3
"""
claude-env :: local approvals UI
File: agents/orchestration/approvals_ui.py
Purpose:
    Gate latency is the #1 friction in approval-based agent systems. This is a
    tiny localhost web page (stdlib http.server only — no new dependencies)
    listing pending approvals with one-click Approve / Deny. Resolutions go
    through the same audited ApprovalGate path as the CLI.

Security posture:
    * binds 127.0.0.1 only — never an external interface
    * every POST requires a per-process CSRF token embedded in the page
    * decided_by is recorded from --by (default $USER) into the ledger

Usage:
    python agents/orchestration/approvals_ui.py                # port 8002
    python agents/orchestration/approvals_ui.py --port 9000 --by alice
    # JSON for scripting:
    curl -s localhost:8002/api/approvals
"""
from __future__ import annotations

import argparse
import html
import json
import os
import secrets
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agents.orchestration.approval_gate import ApprovalGate   # noqa: E402

TOKEN = secrets.token_urlsafe(24)
DECIDED_BY = os.environ.get("USER", "operator")
REGISTRY = _ROOT / "agents" / "agent_registry.yaml"

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>claude-env approvals</title>
<meta http-equiv="refresh" content="20">
<style>
 body{{font-family:ui-monospace,Menlo,monospace;margin:2rem;background:#111;color:#ddd}}
 h1{{font-size:1.1rem}} table{{border-collapse:collapse;width:100%}}
 td,th{{border:1px solid #333;padding:.45rem .6rem;text-align:left;font-size:.85rem}}
 th{{background:#1c1c1c}} .ok{{background:#1d3a1d;color:#cfc;border:0;padding:.35rem .9rem;cursor:pointer}}
 .no{{background:#3a1d1d;color:#fcc;border:0;padding:.35rem .9rem;cursor:pointer}}
 .empty{{color:#777;margin-top:2rem}}
</style></head><body>
<h1>claude-env — pending approvals <small>(auto-refreshes; decisions are audited as “{by}”)</small></h1>
{body}
</body></html>"""


def _rows_html() -> str:
    rows = ApprovalGate.list_open()
    if not rows:
        return '<p class="empty">no pending approvals 🎉</p>'
    out = ["<table><tr><th>requested</th><th>id</th><th>agent</th><th>repo</th>"
           "<th>tier</th><th>action</th><th></th></tr>"]
    for r in rows:
        rid = html.escape(r["request_id"])
        out.append(
            f"<tr><td>{html.escape(str(r['requested_at'])[:19])}</td>"
            f"<td>{rid}</td><td>{html.escape(str(r['agent']))}</td>"
            f"<td>{html.escape(str(r['repo'] or ''))}</td>"
            f"<td>{html.escape(str(r['tier'] if r['tier'] is not None else ''))}</td>"
            f"<td>{html.escape(str(r['action']))[:160]}</td>"
            f"<td><form method=post action=/resolve style='display:inline'>"
            f"<input type=hidden name=id value='{rid}'>"
            f"<input type=hidden name=token value='{TOKEN}'>"
            f"<button class=ok name=decision value=approve>approve</button> "
            f"<button class=no name=decision value=deny>deny</button>"
            f"</form></td></tr>")
    out.append("</table>")
    return "".join(out)


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "text/html"):
        data = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/approvals"):
            self._send(200, json.dumps(ApprovalGate.list_open(), default=str),
                       "application/json")
            return
        self._send(200, PAGE.format(by=html.escape(DECIDED_BY), body=_rows_html()))

    def do_POST(self):  # noqa: N802
        if self.path != "/resolve":
            self._send(404, "not found")
            return
        length = int(self.headers.get("Content-Length", 0))
        form = parse_qs(self.rfile.read(length).decode())
        if form.get("token", [""])[0] != TOKEN:
            self._send(403, "bad token — reload the page")
            return
        rid = form.get("id", [""])[0]
        decision = form.get("decision", [""])[0]
        if rid and decision in ("approve", "deny"):
            gate = ApprovalGate(REGISTRY, session_id="approvals-ui",
                                actor="approvals-ui")
            gate.resolve(rid, approved=(decision == "approve"),
                         decided_by=DECIDED_BY)
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, *a):  # quiet
        pass


def main() -> int:
    global DECIDED_BY
    ap = argparse.ArgumentParser(description="Local approvals web UI")
    ap.add_argument("--port", type=int, default=8002)
    ap.add_argument("--by", default=DECIDED_BY)
    args = ap.parse_args()
    DECIDED_BY = args.by
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"approvals UI -> http://127.0.0.1:{args.port}  (decisions audited as '{DECIDED_BY}')")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
