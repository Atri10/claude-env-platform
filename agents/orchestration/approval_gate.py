#!/usr/bin/env python3
"""
approval_gate.py :: the human-in-the-loop chokepoint.

Decides whether a proposed agent action may proceed automatically, or must be
held for explicit human approval. Every gated action is written to the
`human_approvals` table (via AuditLogger) and blocks until resolved.

A gate fires when ANY of these is true:
  * the agent's registry entry has `requires_approval: true`
  * the action matches a `global_approval_gate` pattern
  * the action writes outside the agent's `write_paths`
  * the action targets a tier-2 or tier-3 repository
  * the action is a state-mutating terminal command
  * the action deletes/prunes memory or rewrites git history

Usage (programmatic):
    gate = ApprovalGate(registry_path, session_id="sess-1", repo="acme", tier=2)
    verdict = gate.evaluate(agent="backend", action="filesystem.write",
                            target="infra/main.tf")
    if verdict.required:
        req_id = gate.open(verdict)          # writes pending approval
        # ... surface req_id to the operator, wait for resolve ...
        gate.resolve(req_id, approved=True, decided_by="operator")

Usage (CLI, to resolve a pending request):
    python approval_gate.py --resolve appr-abc123 --approve --by alice
    python approval_gate.py --list-open
"""
from __future__ import annotations

import argparse
import fnmatch
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# --- repo-root import shim so this runs both as a module and as a script -----
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from audit.audit_logger import AuditLogger  # noqa: E402
from lib.db import get_db  # noqa: E402
from lib.logging_setup import get_logger  # noqa: E402

_log = get_logger("agents")

# Substrings that always indicate a state-mutating / dangerous action.
_STATE_MUTATING_TERMINAL = (
    "terminal.run", "terminal.exec", "terminal.deploy", "terminal.apply",
)
_GIT_REWRITE = ("git push", "git commit --amend", "git rebase", "git reset --hard",
                "git.push", "git.amend", "git.rebase")
_MEMORY_DESTRUCTIVE = ("memory.delete", "memory.prune", "memory_pruner --apply")


@dataclass
class GateVerdict:
    required: bool
    agent: str
    action: str
    target: str | None
    tier: int | None
    reasons: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str:
        tgt = f" -> {self.target}" if self.target else ""
        return f"{self.agent}:{self.action}{tgt} (tier={self.tier})"


class ApprovalGate:
    def __init__(self, registry_path: str | Path, session_id: str,
                 repo: str | None = None, tier: int | None = None,
                 actor: str = "orchestrator"):
        self.registry_path = Path(registry_path)
        reg = yaml.safe_load(self.registry_path.read_text())
        self.agents: dict = reg.get("agents", {})
        self.global_gates: list[str] = reg.get("global_approval_gates", [])
        self.repo = repo
        self.tier = tier
        self.audit = AuditLogger(session_id=session_id, actor=actor,
                                 repo=repo, tier=tier)

    # -- evaluation --------------------------------------------------------
    def evaluate(self, agent: str, action: str,
                 target: str | None = None) -> GateVerdict:
        reasons: list[str] = []
        cfg = self.agents.get(agent, {})

        if cfg.get("requires_approval"):
            reasons.append(f"agent '{agent}' is approval-gated by registry")

        if self.tier is not None and self.tier >= 2:
            reasons.append(f"tier-{self.tier} repository requires approval for all actions")

        if self._is_write(action) and target is not None:
            if not self._within_scope(cfg, target):
                reasons.append(f"write target '{target}' is outside agent write_paths")

        low = action.lower()
        if any(k in low for k in _STATE_MUTATING_TERMINAL):
            reasons.append("state-mutating terminal command")
        if any(k in low for k in _GIT_REWRITE) or (target and any(
                k in str(target).lower() for k in _GIT_REWRITE)):
            reasons.append("git history rewrite / push")
        if any(k in low for k in _MEMORY_DESTRUCTIVE):
            reasons.append("destructive memory operation")

        # denied tools are a hard stop, surfaced as a gate with a deny reason
        for denied in cfg.get("denied_tools", []):
            if fnmatch.fnmatch(action, denied):
                reasons.append(f"action matches denied_tool pattern '{denied}'")

        return GateVerdict(required=bool(reasons), agent=agent, action=action,
                           target=target, tier=self.tier, reasons=reasons)

    @staticmethod
    def _is_write(action: str) -> bool:
        a = action.lower()
        return "write" in a or "delete" in a or "create" in a or a.startswith("filesystem.write")

    @staticmethod
    def _within_scope(cfg: dict, target: str) -> bool:
        scopes = cfg.get("write_paths")
        if not scopes:
            return False  # no declared write scope => any write is out of scope
        t = target.lstrip("./")
        for pat in scopes:
            if fnmatch.fnmatch(t, pat) or fnmatch.fnmatch(t, pat.rstrip("/*") + "/*") \
                    or t == pat:
                return True
        return False

    # -- lifecycle ---------------------------------------------------------
    def open(self, verdict: GateVerdict) -> str:
        """Persist a pending approval, return its request_id."""
        action_desc = f"{verdict.action} :: {'; '.join(verdict.reasons)}"
        if verdict.target:
            action_desc = f"{verdict.action} -> {verdict.target} :: " \
                          f"{'; '.join(verdict.reasons)}"
        req_id = self.audit.human_approval_request(
            agent=verdict.agent, action=action_desc, tier=verdict.tier)
        self._notify(req_id, verdict)
        return req_id

    @staticmethod
    def _notify(req_id: str, verdict: GateVerdict) -> None:
        """Best-effort local notification so gates don't sit unseen (macOS).
        Resolve via `claude-env approvals --list-open` or `claude-env approvals-ui`."""
        if sys.platform != "darwin":
            return
        try:
            import subprocess
            msg = f"{verdict.agent}: {verdict.action}"[:120].replace('"', "'")
            subprocess.run(
                ["osascript", "-e",
                 f'display notification "{msg}" with title '
                 f'"claude-env approval needed ({req_id})"'],
                capture_output=True, timeout=5)
        except Exception:
            # desktop notification is best-effort; the approval still blocks in
            # the UI regardless of whether the toast fires.
            _log.debug("approval desktop notification failed for %s", req_id,
                       exc_info=True)

    def resolve(self, request_id: str, approved: bool, decided_by: str) -> None:
        self.audit.human_approval_resolve(
            request_id, decision="approved" if approved else "denied",
            decided_by=decided_by)

    # -- read helpers ------------------------------------------------------
    @staticmethod
    def list_open() -> list[dict]:
        """Pending approvals, enriched with the session_id from the linked audit
        event so the UI can show full provenance (project/session/command/tier)."""
        db = get_db()
        rows = db.query(
            "SELECT h.request_id, h.agent, h.repo, h.tier, h.action, h.requested_at, "
            "       a.session_id "
            "FROM human_approvals h "
            "LEFT JOIN audit_events a ON a.event_id = h.event_id "
            "WHERE h.decision='pending' ORDER BY h.requested_at")
        return [dict(r) for r in rows]

    @staticmethod
    def list_recent(limit: int = 8) -> list[dict]:
        """Recently resolved approvals (for context in the UI)."""
        db = get_db()
        rows = db.query(
            "SELECT h.request_id, h.agent, h.repo, h.tier, h.action, h.decision, "
            "       h.decided_by, h.decided_at, a.session_id "
            "FROM human_approvals h "
            "LEFT JOIN audit_events a ON a.event_id = h.event_id "
            "WHERE h.decision IN ('approved','denied') "
            "ORDER BY h.decided_at DESC LIMIT ?", [limit])
        return [dict(r) for r in rows]


def _main() -> int:
    ap = argparse.ArgumentParser(description="Approval gate admin")
    ap.add_argument("--registry", default=str(_ROOT / "agents" / "agent_registry.yaml"))
    ap.add_argument("--list-open", action="store_true")
    ap.add_argument("--resolve", metavar="REQUEST_ID")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--deny", action="store_true")
    ap.add_argument("--by", default="operator")
    args = ap.parse_args()

    if args.list_open:
        for r in ApprovalGate.list_open():
            print(f"{r['request_id']}  [{r['agent']}] tier={r['tier']}  {r['action']}")
        return 0

    if args.resolve:
        if not (args.approve or args.deny):
            print("specify --approve or --deny", file=sys.stderr)
            return 2
        gate = ApprovalGate(args.registry, session_id="cli-admin")
        gate.resolve(args.resolve, approved=args.approve, decided_by=args.by)
        print(f"{args.resolve} -> {'approved' if args.approve else 'denied'}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
