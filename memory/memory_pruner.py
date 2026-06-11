"""
claude-env :: memory pruner
File: memory/memory_pruner.py
Purpose:
    Hard-delete memories whose EFFECTIVE confidence has decayed below PRUNE_FLOOR
    and that have not been accessed within STALE_DAYS, EXCEPT decisions and
    architecture nodes (kept for provenance). Superseded nodes past KEEP_SUPERSEDED
    days are also removed. Every prune is audited (operation='prune').
    Decisions/security-relevant memories are archived to JSONL before deletion.

Usage:
    python memory/memory_pruner.py --namespace proj-payments --apply
    python memory/memory_pruner.py --all            # dry-run (default)
"""
from __future__ import annotations
import argparse, json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db
from memory.memory_manager import effective_confidence, _age_days
from audit.audit_logger import AuditLogger

PRUNE_FLOOR = 0.1
STALE_DAYS = 60
KEEP_SUPERSEDED = 30
PROTECTED_KINDS = {"decision", "architecture"}
ARCHIVE = Path.home() / ".claude-env/archive/memory"

def prune_ns(ns: str, apply: bool) -> dict:
    db = get_db(); audit = AuditLogger("pruner", actor="system")
    ARCHIVE.mkdir(parents=True, exist_ok=True)
    rows = db.query("SELECT * FROM memory_nodes WHERE namespace=?", (ns,))
    to_prune = []
    for r in rows:
        eff = effective_confidence(r["confidence"], r["half_life_days"], r["updated_at"])
        stale = _age_days(r["last_access"]) >= STALE_DAYS
        superseded_old = r["superseded_by"] and _age_days(r["updated_at"]) >= KEEP_SUPERSEDED
        if r["node_kind"] in PROTECTED_KINDS:
            continue
        if (eff < PRUNE_FLOOR and stale) or superseded_old:
            to_prune.append(r)
    if apply and to_prune:
        with (ARCHIVE / f"{ns}.jsonl").open("a") as f:
            for r in to_prune:
                f.write(json.dumps({k: r[k] for k in r if k != "embedding"}) + "\n")
        ids = [r["node_id"] for r in to_prune]
        idlist = ",".join(f"'{i}'" for i in ids)
        db.execute(f"DELETE FROM memory_edges WHERE namespace=? AND (src IN ({idlist}) OR dst IN ({idlist}))", (ns,))
        db.execute(f"DELETE FROM memory_nodes WHERE node_id IN ({idlist})")
        for i in ids: audit.memory_write(ns, "any", i, "prune")
    return {"namespace": ns, "pruned" if apply else "would_prune": len(to_prune)}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--namespace"); ap.add_argument("--all", action="store_true")
    ap.add_argument("--apply", action="store_true")
    a = ap.parse_args(); db = get_db()
    nss = ([r["namespace"] for r in db.query("SELECT DISTINCT namespace FROM memory_nodes")]
           if a.all else [a.namespace])
    for ns in nss: print(json.dumps(prune_ns(ns, a.apply)))
