#!/usr/bin/env python3
"""
conflict_resolver.py :: reconcile competing recommendations from multiple agents.

When two or more agents produce overlapping or contradictory proposals (e.g. the
Backend agent wants a synchronous call while Performance wants a queue, or two
agents both edit the same file region), the orchestrator routes the set here.

Resolution is policy-driven and explainable, not a vote. The precedence model:

  1. SECURITY has veto power on anything it flags as a vulnerability.
  2. A hard policy/approval-gate block always wins over a feature proposal.
  3. For design trade-offs, ARCHITECT decisions outrank implementer preferences.
  4. For correctness, TESTING evidence (failing tests) outranks assertions.
  5. Otherwise, prefer the proposal with the narrower blast radius (fewer files,
     reversible, additive) over the broader one.

The resolver returns a Resolution that records the winner, the losers, and the
rationale. Unresolvable conflicts are escalated to the human via the result's
`escalate` flag — the resolver never silently discards a security or policy concern.

Usage:
    r = ConflictResolver(session_id="sess-1")
    res = r.resolve([
        Proposal("backend", "sync call to inventory", files=["src/orders.py"]),
        Proposal("performance", "enqueue to worker", files=["src/orders.py"],
                 evidence="p99 1200ms under load"),
    ])
    print(res.winner, res.rationale, res.escalate)
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from audit.audit_logger import AuditLogger  # noqa: E402

# higher = stronger precedence for design trade-offs / correctness disputes
_PRECEDENCE = {
    "security": 100,      # veto tier (handled explicitly below)
    "architect": 60,
    "testing": 55,
    "database": 40,
    "performance": 38,
    "backend": 30,
    "frontend": 30,
    "devops": 30,
    "documentation": 10,
    "research": 20,
}


@dataclass
class Proposal:
    agent: str
    summary: str
    files: list[str] = field(default_factory=list)
    is_security_block: bool = False   # security flagged a vulnerability
    is_policy_block: bool = False     # policy engine / approval gate blocked it
    evidence: str | None = None       # e.g. benchmark numbers, failing test
    reversible: bool = True
    additive: bool = True

    @property
    def blast_radius(self) -> int:
        # smaller is better; destructive / irreversible adds weight
        r = len(self.files)
        if not self.reversible:
            r += 5
        if not self.additive:
            r += 3
        return r


@dataclass
class Resolution:
    winner: Proposal | None
    losers: list[Proposal]
    rationale: str
    escalate: bool = False


class ConflictResolver:
    def __init__(self, session_id: str, repo: str | None = None,
                 tier: int | None = None):
        self.audit = AuditLogger(session_id=session_id, actor="orchestrator",
                                 repo=repo, tier=tier)

    def resolve(self, proposals: list[Proposal]) -> Resolution:
        if not proposals:
            return Resolution(None, [], "no proposals", escalate=False)
        if len(proposals) == 1:
            return Resolution(proposals[0], [], "single proposal; no conflict")

        res = self._decide(proposals)
        self.audit.agent_action(
            agent="conflict_resolver", action="resolve",
            target=res.winner.agent if res.winner else None,
            summary=res.rationale + (" [ESCALATED]" if res.escalate else ""),
            success=not res.escalate)
        if res.escalate:
            self.audit.security_event(
                category="conflict_escalation", severity="medium",
                detail=res.rationale, source="conflict_resolver")
        return res

    def _decide(self, proposals: list[Proposal]) -> Resolution:
        # 1. security veto: any security block kills conflicting feature proposals
        sec_blocks = [p for p in proposals if p.is_security_block]
        if sec_blocks:
            winner = sec_blocks[0]
            losers = [p for p in proposals if p is not winner]
            return Resolution(winner, losers,
                              "security veto: flagged vulnerability overrides all "
                              "competing proposals")

        # 2. policy / approval block wins over any feature proposal
        pol_blocks = [p for p in proposals if p.is_policy_block]
        if pol_blocks:
            winner = pol_blocks[0]
            losers = [p for p in proposals if p is not winner]
            return Resolution(winner, losers,
                              "policy block overrides feature proposals; the blocked "
                              "action cannot proceed without approval")

        # 3. correctness: a proposal backed by failing-test evidence outranks bare claims
        evidenced = [p for p in proposals if p.evidence]
        bare = [p for p in proposals if not p.evidence]
        if evidenced and bare and self._same_target(proposals):
            winner = max(evidenced, key=lambda p: _PRECEDENCE.get(p.agent, 0))
            losers = [p for p in proposals if p is not winner]
            return Resolution(winner, losers,
                              f"{winner.agent} proposal is backed by evidence "
                              f"({winner.evidence!r}); unevidenced proposals deferred")

        # 4. design trade-off: precedence ranking, architect leads
        ranked = sorted(proposals, key=lambda p: _PRECEDENCE.get(p.agent, 0),
                        reverse=True)
        top, second = ranked[0], ranked[1]
        if _PRECEDENCE.get(top.agent, 0) - _PRECEDENCE.get(second.agent, 0) >= 10:
            losers = [p for p in proposals if p is not top]
            return Resolution(top, losers,
                              f"design precedence: {top.agent} outranks "
                              f"{second.agent} on architectural trade-offs")

        # 5. tie-break on blast radius (narrower, reversible, additive wins)
        by_radius = sorted(proposals, key=lambda p: p.blast_radius)
        if by_radius[0].blast_radius < by_radius[1].blast_radius:
            winner = by_radius[0]
            losers = [p for p in proposals if p is not winner]
            return Resolution(winner, losers,
                              "tie on precedence; selected the narrower, more "
                              "reversible change (smaller blast radius)")

        # genuinely undecidable -> escalate to human
        return Resolution(None, proposals,
                          "proposals are of equal precedence and blast radius with "
                          "no decisive evidence; human decision required",
                          escalate=True)

    @staticmethod
    def _same_target(proposals: list[Proposal]) -> bool:
        sets = [set(p.files) for p in proposals if p.files]
        if len(sets) < 2:
            return False
        base = sets[0]
        return any(base & s for s in sets[1:])


if __name__ == "__main__":
    demo = [
        Proposal("backend", "synchronous inventory check", files=["src/orders.py"]),
        Proposal("performance", "enqueue to background worker",
                 files=["src/orders.py"], evidence="p99 1200ms -> 180ms"),
    ]
    out = ConflictResolver(session_id="cli-demo").resolve(demo)
    win = out.winner.agent if out.winner else "NONE"
    print(f"winner={win} escalate={out.escalate}\n{out.rationale}")
