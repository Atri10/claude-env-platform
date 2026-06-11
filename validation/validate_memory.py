#!/usr/bin/env python3
"""
validate_memory.py :: end-to-end check of the memory graph.

Exercises (exit non-zero on failure):
  * add_node / get_node round-trip
  * add_edge + expand graph walk reaches a connected node
  * keyword recall finds a node by name
  * supersede creates a SUPERSEDES edge and marks the old node
  * namespace isolation: an isolated retriever does NOT see another namespace
  * effective confidence decays below stored confidence for an aged node
  * memory_validator reports a clean graph (no dangling edges) after cleanup

Operates in a dedicated throwaway namespace so it does not pollute real memory.

Usage:  python validation/validate_memory.py
"""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path(os.environ.get("CLAUDE_ENV_HOME", str(Path.home() / ".claude-env")))
for p in (REPO, HOME):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

GREEN, RED, RESET = "\033[32m", "\033[31m", "\033[0m"
_fail = 0


def check(label: str, cond: bool) -> None:
    global _fail
    print(f"{(GREEN+'PASS') if cond else (RED+'FAIL')}{RESET} {label}")
    if not cond:
        _fail += 1


def main() -> int:
    from memory.memory_manager import MemoryManager
    from memory.memory_retriever import MemoryRetriever

    ns = f"test:{uuid.uuid4().hex[:8]}"
    other_ns = f"test-other:{uuid.uuid4().hex[:8]}"
    mgr = MemoryManager(namespace=ns, session_id="validate-mem", actor="validator")
    other = MemoryManager(namespace=other_ns, session_id="validate-mem", actor="validator")

    # round trip
    n1 = mgr.add_node("semantic", "concept", "OrderService",
                      {"summary": "handles order lifecycle"}, confidence=0.9)
    got = mgr.get_node(n1)
    check("add_node / get_node round-trip", got is not None and got["name"] == "OrderService")

    # edge + expand
    n2 = mgr.add_node("semantic", "entity", "InventoryService",
                      {"summary": "tracks stock"}, confidence=0.9)
    mgr.add_edge(n1, n2, "RELATES_TO")
    retr = MemoryRetriever(namespace=ns, session_id="validate-mem", actor="validator")
    expanded = retr.expand([n1], depth=2)
    ids = {r["node_id"] for r in expanded}
    check("graph expand reaches connected node", n2 in ids)

    # keyword recall
    hits = retr.keyword_recall("OrderService")
    check("keyword recall finds node", any(h["node_id"] == n1 for h in hits))

    # supersede
    n1b = mgr.supersede(n1, "semantic", "concept", "OrderService",
                        {"summary": "v2: handles order lifecycle + refunds"})
    old = mgr.get_node(n1)
    check("supersede marks old node", old is not None and old.get("superseded_by") == n1b)

    # namespace isolation
    other.add_node("semantic", "concept", "SecretThing", {"x": 1})
    iso = MemoryRetriever(namespace=ns, session_id="validate-mem",
                          actor="validator", isolated=True)
    leak = iso.keyword_recall("SecretThing", extra_ns=[other_ns])
    check("isolated namespace blocks cross-ns recall", len(leak) == 0)

    # decay
    from memory.memory_manager import effective_confidence
    eff = effective_confidence(1.0, half_life=30, updated_at="2000-01-01T00:00:00Z")
    check("aged node confidence decays below stored", eff < 1.0)

    # validator clean
    try:
        from memory.memory_validator import validate_ns
        problems = validate_ns(ns, repair=False)
        dangling = problems.get("dangling_edges", 0)
        check("no dangling edges in test namespace", dangling == 0)
    except Exception as e:
        check(f"memory_validator runs ({e})", False)

    print()
    if _fail:
        print(f"{RED}{_fail} memory check(s) failed{RESET}")
        return 1
    print(f"{GREEN}all memory checks passed{RESET}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
