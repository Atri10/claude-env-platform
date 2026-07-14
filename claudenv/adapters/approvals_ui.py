#!/usr/bin/env python3
"""
claude-env :: local approvals UI

Gate latency is the #1 friction in approval-based agent systems. This is a
single localhost web page (stdlib http.server only — no new dependencies)
that shows every pending approval as a numbered queue — position, repo,
agent, tier, session, the exact command, and an absolute request timestamp
— with one-click Approve / Deny, plus a recent-decisions log. It is designed
to live in ONE browser tab: the terminal server opens it once when it first
starts and never again, so requests accumulate in this page rather than
spawning a new tab each time. Resolutions go through the same audited
ApprovalGate path as the CLI. There is no desktop notification.

Security posture:
    * binds 127.0.0.1 only — never an external interface
    * every POST requires a per-process CSRF token embedded in the page
    * decided_by is recorded automatically as the OS user @ host (no name is
      asked for)

Usage:
    python -m claudenv.adapters.approvals_ui                # port 8002
    python -m claudenv.adapters.approvals_ui --port 9000
    curl -s localhost:8002/api/approvals                    # JSON for scripting
"""
from __future__ import annotations

import argparse
import getpass
import html
import json
import secrets
import socket
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs

from claudenv.adapters.config import get_config
from claudenv.adapters.services import FileServiceRegistry, bind_http
from claudenv.application.approval import ApprovalGate
from claudenv.ports.approval import IApprovalGate

TOKEN = secrets.token_urlsafe(24)


def _default_decider() -> str:
    try:
        return f"{getpass.getuser()}@{socket.gethostname()}"
    except Exception:
        return "operator"


DECIDED_BY = _default_decider()

_TIER_LABEL = {0: "public", 1: "internal", 2: "sensitive", 3: "restricted"}

CSS = """
:root{
  --bg:#f6f7f9; --panel:#ffffff; --ink:#1c2024; --muted:#6b7280; --line:#e5e7eb;
  --code-bg:#0f172a; --code-ink:#e2e8f0; --accent:#2563eb;
  --ok:#15803d; --ok-bg:#dcfce7; --no:#b91c1c; --no-bg:#fee2e2;
  --t0:#6b7280; --t1:#2563eb; --t2:#b45309; --t3:#b91c1c;
}
@media (prefers-color-scheme: dark){
  :root{ --bg:#0b0e14; --panel:#141922; --ink:#e6e9ef; --muted:#9aa4b2; --line:#232a36;
         --code-bg:#0a0f1a; --code-ink:#d7e0ee; --accent:#60a5fa;
         --ok:#4ade80; --ok-bg:#0f2a1a; --no:#f87171; --no-bg:#2a1414; }
}
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  margin:0;background:var(--bg);color:var(--ink);line-height:1.45}
.wrap{max-width:900px;margin:0 auto;padding:1.5rem 1.25rem 3rem}
header{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;
  flex-wrap:wrap;margin-bottom:1.25rem}
h1{font-size:1.15rem;margin:0;font-weight:650}
.sub{color:var(--muted);font-size:.82rem}
.sub b{color:var(--ink);font-weight:600}
h2{font-size:.8rem;text-transform:uppercase;letter-spacing:.06em;color:var(--muted);
  margin:1.75rem 0 .6rem}
.qcount{display:inline-flex;align-items:center;justify-content:center;min-width:1.4rem;
  height:1.4rem;padding:0 .45rem;border-radius:999px;background:var(--accent);color:#fff;
  font-size:.78rem;font-weight:700;margin-left:.4rem}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;
  padding:1rem 1.1rem;margin-bottom:.85rem;box-shadow:0 1px 2px rgba(0,0,0,.04)}
.card.head{border-left:3px solid var(--accent)}
.top{display:flex;align-items:center;gap:.55rem;flex-wrap:wrap;margin-bottom:.6rem}
.qpos{display:inline-flex;align-items:center;justify-content:center;min-width:1.5rem;
  height:1.5rem;border-radius:8px;background:var(--line);color:var(--ink);
  font-size:.78rem;font-weight:700;font-family:ui-monospace,Menlo,monospace}
.proj{font-weight:650;font-size:1rem}
.pill{font-size:.68rem;font-weight:600;padding:.12rem .5rem;border-radius:999px;
  border:1px solid var(--line);color:#fff}
.pill.t0{background:var(--t0)} .pill.t1{background:var(--t1)}
.pill.t2{background:var(--t2)} .pill.t3{background:var(--t3)}
.rid{font-family:ui-monospace,Menlo,monospace;font-size:.72rem;color:var(--muted);margin-left:auto}
.meta{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
  gap:.3rem .9rem;color:var(--muted);font-size:.8rem;margin-bottom:.6rem}
.meta .k{color:var(--muted)}
.meta b{color:var(--ink);font-weight:600}
.cmd{background:var(--code-bg);color:var(--code-ink);font-family:ui-monospace,Menlo,monospace;
  font-size:.82rem;padding:.6rem .75rem;border-radius:8px;white-space:pre-wrap;
  word-break:break-word;margin:.1rem 0 .8rem}
.actions{display:flex;gap:.5rem}
button{font:inherit;font-size:.85rem;font-weight:600;border:1px solid transparent;
  border-radius:8px;padding:.45rem 1.1rem;cursor:pointer}
.ok{background:var(--ok-bg);color:var(--ok);border-color:var(--ok)}
.no{background:var(--no-bg);color:var(--no);border-color:var(--no)}
button:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.empty{color:var(--muted);text-align:center;padding:2.5rem 0;font-size:.95rem}
.recent{width:100%;border-collapse:collapse;font-size:.8rem}
.recent td,.recent th{padding:.4rem .5rem;border-bottom:1px solid var(--line);text-align:left}
.recent th{color:var(--muted);font-weight:600}
.dc-approved{color:var(--ok);font-weight:600}
.dc-denied{color:var(--no);font-weight:600}
.mono{font-family:ui-monospace,Menlo,monospace}
"""


def _ts(iso: str | None) -> str:
    """Absolute, human-readable local timestamp — no relative 'N min ago'."""
    if not iso:
        return "—"
    try:
        t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        if t.tzinfo is None:
            t = t.replace(tzinfo=UTC)
        local = t.astimezone()
        return local.strftime("%Y-%m-%d %H:%M:%S %Z")
    except Exception:
        return html.escape(str(iso)[:19])


def _tier_pill(tier) -> str:
    if tier is None or tier == "":
        return '<span class="pill t0">tier ?</span>'
    t = int(tier)
    return f'<span class="pill t{t}">tier {t} · {_TIER_LABEL.get(t, "?")}</span>'


def _command_of(action: str) -> str:
    """Show the command; strip the 'terminal.run: ' prefix for readability."""
    return action.split("terminal.run:", 1)[1].strip() if "terminal.run:" in action else action


def _pending_html(gate: IApprovalGate) -> str:
    rows = gate.list_open()  # ordered by requested_at (oldest first) == queue order
    if not rows:
        return ('<h2>Pending queue</h2>'
                '<div class="empty">✓ No pending approvals — you\'re all caught up.</div>')
    out = [f'<h2>Pending queue<span class="qcount">{len(rows)}</span></h2>']
    for i, r in enumerate(rows, start=1):
        rid = html.escape(str(r["request_id"]))
        proj = html.escape(str(r.get("repo") or "—"))
        agent = html.escape(str(r.get("agent") or "—"))
        session = html.escape(str(r.get("session_id") or "—"))
        tier_val = r.get("tier")
        tier_txt = html.escape(_TIER_LABEL.get(int(tier_val), "?")
                                if tier_val not in (None, "") else "unknown")
        ts = html.escape(_ts(r.get("requested_at")))
        cmd = html.escape(_command_of(str(r.get("action") or "")))
        head = ' head' if i == 1 else ''
        out.append(
            f'<div class="card{head}">'
            f'<div class="top"><span class="qpos">{i}</span>'
            f'<span class="proj">{proj}</span>'
            f'{_tier_pill(tier_val)}'
            f'<span class="rid">{rid}</span></div>'
            '<div class="meta">'
            f'<span class="k">repo <b>{proj}</b></span>'
            f'<span class="k">agent <b>{agent}</b></span>'
            f'<span class="k">tier <b>{tier_txt}</b></span>'
            f'<span class="k">session <b class="mono">{session}</b></span>'
            f'<span class="k">requested <b class="mono">{ts}</b></span>'
            '</div>'
            f'<div class="cmd">{cmd}</div>'
            '<form method="post" action="/resolve" class="actions">'
            f'<input type="hidden" name="id" value="{rid}">'
            f'<input type="hidden" name="token" value="{TOKEN}">'
            '<button class="ok" name="decision" value="approve">Approve</button>'
            '<button class="no" name="decision" value="deny">Deny</button>'
            '</form></div>')
    return "".join(out)


def _recent_html(gate: IApprovalGate) -> str:
    rows = gate.list_recent(limit=8)
    if not rows:
        return ""
    body = ["<h2>Recent decisions</h2><table class='recent'>"
            "<tr><th>when</th><th>decision</th><th>by</th><th>repo</th>"
            "<th>command</th></tr>"]
    for r in rows:
        dc = html.escape(str(r.get("decision") or ""))
        body.append(
            "<tr>"
            f"<td class='mono'>{html.escape(_ts(r.get('decided_at')))}</td>"
            f"<td class='dc-{dc}'>{dc}</td>"
            f"<td class='mono'>{html.escape(str(r.get('decided_by') or '—'))}</td>"
            f"<td>{html.escape(str(r.get('repo') or '—'))}</td>"
            f"<td class='mono'>{html.escape(_command_of(str(r.get('action') or ''))[:80])}</td>"
            "</tr>")
    body.append("</table>")
    return "".join(body)


def _page(gate: IApprovalGate) -> str:
    by = html.escape(DECIDED_BY)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>claude-env approvals</title>'
        '<meta http-equiv="refresh" content="5">'
        f'<style>{CSS}</style></head><body><div class="wrap">'
        '<header><div><h1>claude-env — approvals</h1>'
        '<div class="sub">One queue, one tab · auto-refreshes every 5s · localhost only</div></div>'
        f'<div class="sub">signed in as <b class="mono">{by}</b></div></header>'
        f'{_pending_html(gate)}{_recent_html(gate)}'
        '</div></body></html>')


def make_handler(gate: IApprovalGate) -> type[BaseHTTPRequestHandler]:
    """Bind the approval gate into a Handler class (http.server wants a class,
    not an instance, so the gate is captured via closure)."""

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
                self._send(200, json.dumps(gate.list_open(), default=str), "application/json")
                return
            self._send(200, _page(gate))

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
                gate.resolve(rid, approved=(decision == "approve"), decided_by=DECIDED_BY)
            self.send_response(303)
            self.send_header("Location", "/")
            self.end_headers()

        def log_message(self, *a):  # quiet
            pass

    return Handler


def main() -> int:
    global DECIDED_BY
    ap = argparse.ArgumentParser(description="Local approvals web UI")
    ap.add_argument("--port", type=int, default=8002,
                     help="preferred port; falls back to a free one if busy (0 = random)")
    ap.add_argument("--by", default=None,
                     help="override the recorded approver (default: OS user@host)")
    args = ap.parse_args()
    if args.by:
        DECIDED_BY = args.by

    from claudenv.adapters.persistence import SQLiteDatabase
    from claudenv.di import get_container
    from claudenv.ports import IAuditLogger

    config = get_config()
    db = SQLiteDatabase(config.get_database_dsn())
    audit = get_container().get(IAuditLogger)
    gate = ApprovalGate(audit, db)
    registry = FileServiceRegistry(config.get_claude_env_home())

    srv, port = bind_http(make_handler(gate), args.port)
    registry.register("approvals", port, extra={"decided_by": DECIDED_BY})
    print(f"approvals UI -> http://127.0.0.1:{port}  "
          f"(decisions recorded as '{DECIDED_BY}')")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye")
    finally:
        registry.unregister("approvals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
