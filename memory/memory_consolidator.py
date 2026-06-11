"""
claude-env :: memory consolidator
File: memory/memory_consolidator.py
Purpose:
    Reduce graph clutter by merging clusters of related low-value nodes into a
    single higher-level summary node. Runs nightly (launchd). Conservative:
    only consolidates nodes that are (a) same memory_type, (b) connected by
    RELATES_TO, (c) below a confidence ceiling, (d) older than min_age_days.
    The summary node links to its sources via CONSOLIDATES edges so provenance
    is preserved; sources are marked superseded (not deleted).

Usage:
    python memory/memory_consolidator.py --namespace proj-payments
    python memory/memory_consolidator.py --all
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db
from memory.memory_manager import MemoryManager, effective_confidence, _age_days

CONF_CEILING = 0.5
MIN_AGE_DAYS = 14
MIN_CLUSTER = 3

def _clusters(db, ns):
    """Find weakly-connected RELATES_TO clusters of eligible nodes."""
    nodes = db.query("SELECT node_id,memory_type,confidence,half_life_days,updated_at,name,body_json "
                     "FROM memory_nodes WHERE namespace=? AND superseded_by IS NULL", (ns,))
    eligible = {n["node_id"]: n for n in nodes
                if effective_confidence(n["confidence"], n["half_life_days"], n["updated_at"]) < CONF_CEILING
                and _age_days(n["updated_at"]) >= MIN_AGE_DAYS}
    edges = db.query("SELECT src,dst FROM memory_edges WHERE namespace=? AND rel='RELATES_TO'", (ns,))
    adj = {nid: set() for nid in eligible}
    for e in edges:
        if e["src"] in eligible and e["dst"] in eligible:
            adj[e["src"]].add(e["dst"]); adj[e["dst"]].add(e["src"])
    seen, clusters = set(), []
    for nid in eligible:
        if nid in seen: continue
        stack, comp = [nid], []
        while stack:
            cur = stack.pop()
            if cur in seen: continue
            seen.add(cur); comp.append(cur)
            stack.extend(adj[cur] - seen)
        # group by memory_type within component
        by_type = {}
        for c in comp: by_type.setdefault(eligible[c]["memory_type"], []).append(c)
        for mt, members in by_type.items():
            if len(members) >= MIN_CLUSTER:
                clusters.append((mt, members, [eligible[m] for m in members]))
    return clusters

def consolidate_ns(ns: str) -> dict:
    db = get_db(); mm = MemoryManager(ns, session_id="consolidator", actor="system")
    made = 0
    for mt, ids, members in _clusters(db, ns):
        names = [m["name"] for m in members][:8]
        summary = {"consolidated_from": ids,
                   "summary": f"Cluster of {len(ids)} related {mt} memories: " + "; ".join(names)}
        new_id = mm.add_node(mt, "concept", f"[consolidated] {mt} cluster", summary,
                             confidence=max(m["confidence"] for m in members))
        for old in ids:
            mm.add_edge(new_id, old, "CONSOLIDATES")
            db.execute("UPDATE memory_nodes SET superseded_by=? WHERE node_id=?", (new_id, old))
            mm.audit.memory_write(ns, mt, old, "consolidate")
        made += 1
    return {"namespace": ns, "clusters_consolidated": made}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace"); ap.add_argument("--all", action="store_true")
    a = ap.parse_args(); db = get_db()
    if a.all:
        nss = [r["namespace"] for r in db.query("SELECT DISTINCT namespace FROM memory_nodes")]
    else:
        nss = [a.namespace]
    for ns in nss: print(json.dumps(consolidate_ns(ns)))
