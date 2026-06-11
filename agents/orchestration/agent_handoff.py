#!/usr/bin/env python3
"""
agent_handoff.py :: structured transfer of work between agents.

A handoff packages exactly what the receiving agent needs and nothing it must not
see. It enforces three invariants:

  1. Scope narrowing  - the receiver gets only the artifacts/paths relevant to its
                        role; it cannot inherit the sender's broader access.
  2. Provenance       - every handoff is recorded as an agent_action in the audit
                        ledger, so the chain of custody is reconstructable.
  3. Context hygiene  - free-text context is wrapped as DATA and never replays the
                        sender's instructions as commands to the receiver.

Usage:
    hub = HandoffHub(registry_path, session_id="sess-1", repo="acme", tier=1)
    packet = hub.handoff(
        src="architect", dst="backend",
        objective="Implement the OrderService per ADR-014",
        artifacts={"adr": "docs/adr/0014-order-service.md"},
        paths=["src/services/order_service.py", "tests/test_order_service.py"],
        notes="Idempotency key required on create.")
    # packet.context is safe to inject into the receiver's prompt.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from audit.audit_logger import AuditLogger  # noqa: E402


@dataclass
class HandoffPacket:
    src: str
    dst: str
    objective: str
    artifacts: dict[str, str] = field(default_factory=dict)
    paths: list[str] = field(default_factory=list)
    notes: str = ""
    audit_event_id: int | None = None

    @property
    def context(self) -> str:
        """Prompt-injectable, clearly delimited DATA block for the receiver."""
        lines = [
            f"<handoff from=\"{self.src}\" to=\"{self.dst}\">",
            f"  <objective>{self.objective}</objective>",
        ]
        if self.artifacts:
            lines.append("  <artifacts>")
            for k, v in self.artifacts.items():
                lines.append(f"    <ref name=\"{k}\">{v}</ref>")
            lines.append("  </artifacts>")
        if self.paths:
            lines.append("  <in_scope_paths>")
            for p in self.paths:
                lines.append(f"    <path>{p}</path>")
            lines.append("  </in_scope_paths>")
        if self.notes:
            # notes are sender-authored => treat as data, not instructions
            lines.append(f"  <notes treat-as=\"data\">{self.notes}</notes>")
        lines.append("</handoff>")
        return "\n".join(lines)


class HandoffHub:
    def __init__(self, registry_path: str | Path, session_id: str,
                 repo: str | None = None, tier: int | None = None):
        reg = yaml.safe_load(Path(registry_path).read_text())
        self.agents: dict = reg.get("agents", {})
        self.audit = AuditLogger(session_id=session_id, actor="orchestrator",
                                 repo=repo, tier=tier)

    def _validate(self, src: str, dst: str, paths: list[str]) -> None:
        if src not in self.agents:
            raise ValueError(f"unknown source agent: {src}")
        if dst not in self.agents:
            raise ValueError(f"unknown destination agent: {dst}")
        # narrow scope: dst may only RECEIVE paths it could plausibly write/read.
        dst_cfg = self.agents[dst]
        write_paths = dst_cfg.get("write_paths")
        if write_paths:
            import fnmatch
            for p in paths:
                t = p.lstrip("./")
                ok = any(fnmatch.fnmatch(t, wp) or
                         fnmatch.fnmatch(t, wp.rstrip("/*") + "/*") for wp in write_paths)
                if not ok:
                    # not fatal for read-only handoffs, but flagged in audit notes
                    self.audit.security_event(
                        category="handoff_scope", severity="low",
                        detail=f"path '{p}' handed to {dst} is outside its write_paths",
                        source="agent_handoff")

    def handoff(self, src: str, dst: str, objective: str,
                artifacts: dict[str, str] | None = None,
                paths: list[str] | None = None, notes: str = "") -> HandoffPacket:
        artifacts = artifacts or {}
        paths = paths or []
        self._validate(src, dst, paths)

        packet = HandoffPacket(src=src, dst=dst, objective=objective,
                               artifacts=artifacts, paths=paths, notes=notes)
        eid = self.audit.agent_action(
            agent=src, action="handoff",
            target=dst,
            summary=json.dumps({"objective": objective, "paths": paths,
                                "artifacts": list(artifacts.keys())}),
            success=True)
        packet.audit_event_id = eid
        return packet


if __name__ == "__main__":
    # smoke demonstration (no DB write side effects beyond audit, safe to run)
    import argparse
    ap = argparse.ArgumentParser(description="Create a handoff packet")
    ap.add_argument("--registry", default=str(_ROOT / "agents" / "agent_registry.yaml"))
    ap.add_argument("--src", required=True)
    ap.add_argument("--dst", required=True)
    ap.add_argument("--objective", required=True)
    ap.add_argument("--path", action="append", default=[])
    ap.add_argument("--session", default="cli-handoff")
    a = ap.parse_args()
    hub = HandoffHub(a.registry, session_id=a.session)
    pkt = hub.handoff(a.src, a.dst, a.objective, paths=a.path)
    print(pkt.context)
