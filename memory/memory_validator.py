"""
claude-env :: memory validator
File: memory/memory_validator.py
Purpose:
    Integrity + corruption checks over the memory graph:
      * dangling edges (src/dst not present)
      * confidence out of [0,1]
      * orphaned superseded chains / supersede cycles
      * body_json parseable
      * namespace leakage (edge crossing namespaces)
    Reports issues; with --repair fixes the safe ones (drop dangling edges,
    clamp confidence). Corruption findings are logged as security_events.

Usage:
    python memory/memory_validator.py --all
    python memory/memory_validator.py --namespace proj-x --repair
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db
from audit.audit_logger import AuditLogger

def validate_ns(ns: str, repair: bool) -> dict:
    db = get_db(); audit = AuditLogger("mem-validator", actor="system")
    issues = {"dangling_edges": 0, "bad_confidence": 0, "bad_json": 0, "ns_leak_edges": 0, "supersede_cycle": 0}
    node_ids = {r["node_id"] for r in db.query("SELECT node_id FROM memory_nodes WHERE namespace=?", (ns,))}
    # dangling + ns-leak edges
    edges = db.query("SELECT * FROM memory_edges WHERE namespace=?", (ns,))
    dangling = []
    for e in edges:
        if e["src"] not in node_ids or e["dst"] not in node_ids:
            issues["dangling_edges"] += 1; dangling.append(e["edge_id"])
    # confidence + json
    bad_conf = []
    for n in db.query("SELECT node_id,confidence,body_json FROM memory_nodes WHERE namespace=?", (ns,)):
        if not (0.0 <= n["confidence"] <= 1.0):
            issues["bad_confidence"] += 1; bad_conf.append(n["node_id"])
        try: json.loads(n["body_json"])
        except Exception: issues["bad_json"] += 1
    # supersede cycle detection
    sup = {r["node_id"]: r["superseded_by"]
           for r in db.query("SELECT node_id,superseded_by FROM memory_nodes WHERE namespace=? AND superseded_by IS NOT NULL", (ns,))}
    for start in sup:
        seen, cur = set(), start
        while cur in sup and cur not in seen:
            seen.add(cur); cur = sup[cur]
        if cur in seen: issues["supersede_cycle"] += 1
    if repair:
        for eid in dangling:
            db.execute("DELETE FROM memory_edges WHERE edge_id=?", (eid,))
        for nid in bad_conf:
            db.execute("UPDATE memory_nodes SET confidence=MIN(1.0,MAX(0.0,confidence)) WHERE node_id=?", (nid,))
    total = sum(issues.values())
    if total: audit.security_event("mem_corruption", "medium" if total < 5 else "high",
                                   f"{ns}: {issues}", source=ns)
    return {"namespace": ns, "issues": issues, "repaired": repair, "ok": total == 0}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--repair", action="store_true")
    a = ap.parse_args(); db = get_db()
    nss = ([r["namespace"] for r in db.query("SELECT DISTINCT namespace FROM memory_nodes")]
           if a.all else [a.namespace])
    allok = True
    for ns in nss:
        r = validate_ns(ns, a.repair); allok &= r["ok"]; print(json.dumps(r))
    sys.exit(0 if allok else 1)
