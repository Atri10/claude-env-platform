#!/usr/bin/env python3
"""
task_router.py :: maps a task to the specialist agent that should own it.

The router is deliberately deterministic and explainable: it scores each agent
against the task using (1) explicit intent verbs, (2) target-path matching against
the agent's declared write_paths, and (3) a read-only vs write capability check.
It returns a ranked RoutingDecision list; the orchestrator picks the top entry
(or asks for clarification when the top two are within a small margin).

Routing never grants permission. The chosen agent is still subject to the
ApprovalGate and the per-repo policy engine before any action executes.

Usage:
    router = TaskRouter(registry_path)
    decision = router.route("Add a POST /orders endpoint with validation",
                            target="src/api/orders.py")
    print(decision.agent, decision.score, decision.rationale)

CLI:
    python task_router.py "write integration tests for the auth module"
"""
from __future__ import annotations

import argparse
import fnmatch
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Intent keywords per agent. Weighted: a hit adds the weight to the agent score.
_INTENT: dict[str, list[tuple[str, float]]] = {
    "architect": [(r"\barchitect|design|adr|rfc|boundary|trade-?off|diagram|coupling\b", 3)],
    "backend": [(r"\bapi|endpoint|service|business logic|handler|controller|model\b", 3),
                (r"\bserver|backend|rest|grpc|queue\b", 2)],
    "frontend": [(r"\bui|component|css|styling|accessibility|a11y|page|render|state\b", 3),
                 (r"\bfrontend|react|vue|svelte|tailwind\b", 2)],
    "database": [(r"\bschema|migration|index|query|table|sql|ddl|normaliz\b", 3),
                 (r"\bdatabase|postgres|sqlite\b", 2)],
    "devops": [(r"\bci/?cd|pipeline|docker|container|terraform|infra|deploy|workflow\b", 3),
               (r"\bdevops|kubernetes|helm|monitoring setup\b", 2)],
    "security": [(r"\bsecurity|threat[ -]?model|vulnerab|cve|secret scan|sast|audit\b", 3),
                 (r"\binjection|authz|authn|ssrf|exploit\b", 2)],
    "performance": [(r"\bperformance|profil|benchmark|latency|hot ?path|optimi|complexity\b", 3)],
    "testing": [(r"\btest|coverage|mutation|unit test|integration test|e2e|fixture\b", 3)],
    "documentation": [(r"\bdocs?|documentation|readme|changelog|adr|rfc\b", 2)],
    "research": [(r"\bresearch|evaluate|compare libraries|investigate|survey|which library\b", 3)],
}

# Agents that may write (used to penalize routing a write task to a read-only agent).
_WRITE_AGENTS = {"backend", "frontend", "database", "devops", "testing", "documentation"}
_WRITE_INTENT = re.compile(r"\b(write|add|implement|create|edit|fix|refactor|build|update|"
                           r"author|generate|modify)\b", re.I)


@dataclass
class RoutingDecision:
    agent: str
    score: float
    rationale: str


class TaskRouter:
    def __init__(self, registry_path: str | Path):
        reg = yaml.safe_load(Path(registry_path).read_text())
        self.agents: dict = reg.get("agents", {})

    def rank(self, task: str, target: str | None = None) -> list[RoutingDecision]:
        task_l = task.lower()
        wants_write = bool(_WRITE_INTENT.search(task_l))
        out: list[RoutingDecision] = []

        for agent, cfg in self.agents.items():
            if agent == "orchestrator":
                continue
            score = 0.0
            why: list[str] = []

            for pat, w in _INTENT.get(agent, []):
                if re.search(pat, task_l):
                    score += w
                    why.append(f"intent match (+{w})")

            # path-based signal: target inside this agent's write_paths
            if target:
                for wp in cfg.get("write_paths", []):
                    t = target.lstrip("./")
                    if fnmatch.fnmatch(t, wp) or fnmatch.fnmatch(t, wp.rstrip("/*") + "/*"):
                        score += 2.5
                        why.append(f"target in write_paths '{wp}' (+2.5)")
                        break

            # capability alignment
            if wants_write and agent not in _WRITE_AGENTS and score > 0:
                score -= 2.0
                why.append("write task but agent is read-only (-2.0)")
            if not wants_write and agent in {"architect", "security",
                                             "performance", "research"} and score > 0:
                score += 0.5
                why.append("analysis task suits read-only specialist (+0.5)")

            if score > 0:
                out.append(RoutingDecision(agent, round(score, 2),
                                           "; ".join(why) or "weak match"))

        out.sort(key=lambda d: d.score, reverse=True)
        return out

    def route(self, task: str, target: str | None = None) -> RoutingDecision:
        ranked = self.rank(task, target)
        if not ranked:
            return RoutingDecision("orchestrator", 0.0,
                                   "no specialist matched; orchestrator handles directly")
        return ranked[0]

    def needs_clarification(self, task: str, target: str | None = None,
                            margin: float = 1.0) -> bool:
        ranked = self.rank(task, target)
        return len(ranked) >= 2 and (ranked[0].score - ranked[1].score) < margin


def _main() -> int:
    ap = argparse.ArgumentParser(description="Route a task to a specialist agent")
    ap.add_argument("task")
    ap.add_argument("--target", default=None, help="primary file/path the task touches")
    ap.add_argument("--registry", default=str(_ROOT / "agents" / "agent_registry.yaml"))
    ap.add_argument("--all", action="store_true", help="show full ranking")
    args = ap.parse_args()

    router = TaskRouter(args.registry)
    if args.all:
        for d in router.rank(args.task, args.target):
            print(f"{d.score:5.2f}  {d.agent:14s}  {d.rationale}")
        return 0

    d = router.route(args.task, args.target)
    flag = "  [AMBIGUOUS]" if router.needs_clarification(args.task, args.target) else ""
    print(f"-> {d.agent} (score {d.score}){flag}\n   {d.rationale}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
