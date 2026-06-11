#!/usr/bin/env python3
"""
validate_agents.py :: verify the multi-agent framework is internally consistent.

Checks (exit non-zero on failure):
  * agent_registry.yaml parses and defines all 10 specialists + orchestrator
  * every agent references a prompt file that exists
  * orchestrator.can_spawn lists exactly the 10 specialists
  * task router sends representative tasks to the expected specialist
  * approval gate fires for: devops (always), tier-2/3 actions, out-of-scope writes,
    state-mutating terminal, git history rewrite, memory prune
  * approval gate does NOT fire for an in-scope backend edit in a tier-0 repo
  * conflict resolver gives security veto precedence and escalates a true stalemate

Usage:  python validation/validate_agents.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (REPO, HOME):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

REGISTRY = REPO / "agents" / "agent_registry.yaml"
GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
_fail = 0

SPECIALISTS = {"architect", "backend", "frontend", "database", "devops",
               "security", "performance", "testing", "documentation", "research"}


def check(label: str, cond: bool) -> None:
    global _fail
    print(f"{(GREEN+'PASS') if cond else (RED+'FAIL')}{RESET} {label}")
    if not cond:
        _fail += 1


def main() -> int:
    reg = yaml.safe_load(REGISTRY.read_text())
    agents = reg.get("agents", {})

    check("registry defines orchestrator", "orchestrator" in agents)
    check("registry defines all 10 specialists", SPECIALISTS <= set(agents))

    # prompt files exist
    for name, cfg in agents.items():
        rel = cfg.get("prompt", "")
        check(f"prompt exists for {name}", bool(rel) and (REPO / rel).exists())

    # orchestrator spawn set
    spawn = set(agents.get("orchestrator", {}).get("can_spawn", []))
    check("orchestrator can_spawn == specialists", spawn == SPECIALISTS)

    # --- task router ---
    from agents.orchestration.task_router import TaskRouter
    router = TaskRouter(REGISTRY)
    cases = [
        ("Add a POST /orders endpoint with validation", "src/api/orders.py", "backend"),
        ("Write integration tests for the auth module", "tests/test_auth.py", "testing"),
        ("Design a schema and migration for invoices", "migrations/004.sql", "database"),
        ("Add a CI pipeline that runs lint and tests", ".github/workflows/ci.yml", "devops"),
        ("Threat-model the new payment flow", None, "security"),
        ("Write an ADR documenting the cache decision", "docs/adr/0007.md", "documentation"),
    ]
    for task, target, expected in cases:
        chosen = router.route(task, target).agent
        check(f"router: {expected!r} for {task[:38]!r}", chosen == expected)

    # --- approval gate ---
    from agents.orchestration.approval_gate import ApprovalGate

    gate2 = ApprovalGate(REGISTRY, session_id="validate-agents", repo="acme", tier=2)
    check("gate fires on tier-2 action",
          gate2.evaluate("backend", "filesystem.write", "src/app.py").required)

    gate0 = ApprovalGate(REGISTRY, session_id="validate-agents", repo="oss", tier=0)
    check("gate fires for devops always",
          gate0.evaluate("devops", "filesystem.write", "infra/main.tf").required)
    check("gate fires on out-of-scope write",
          gate0.evaluate("backend", "filesystem.write", "infra/main.tf").required)
    check("gate fires on state-mutating terminal",
          gate0.evaluate("devops", "terminal.run", "kubectl apply").required)
    check("gate fires on git rewrite",
          gate0.evaluate("backend", "git.push", "origin main").required)
    check("gate fires on memory prune",
          gate0.evaluate("orchestrator", "memory.prune", None).required)
    check("gate quiet for in-scope tier-0 backend edit",
          not gate0.evaluate("backend", "filesystem.write", "src/app.py").required)

    # --- conflict resolver ---
    from agents.orchestration.conflict_resolver import ConflictResolver, Proposal
    cr = ConflictResolver(session_id="validate-agents")
    res = cr.resolve([
        Proposal("backend", "ship sync call", files=["src/o.py"]),
        Proposal("security", "blocked: SSRF risk", files=["src/o.py"],
                 is_security_block=True),
    ])
    check("security veto wins conflict", res.winner and res.winner.agent == "security")

    stalemate = cr.resolve([
        Proposal("backend", "approach A", files=["src/a.py"]),
        Proposal("frontend", "approach B", files=["src/b.py"]),
    ])
    check("true stalemate escalates", stalemate.escalate)

    print()
    if _fail:
        print(f"{RED}{_fail} agent check(s) failed{RESET}")
        return 1
    print(f"{GREEN}all agent checks passed{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
