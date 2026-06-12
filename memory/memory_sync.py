#!/usr/bin/env python3
"""
claude-env :: memory namespace export / import (team knowledge sync)
File: memory/memory_sync.py
Purpose:
    Move accumulated team knowledge (decisions, conventions, architecture
    nodes, session learnings) between machines as a reviewable JSONL file.
    Typical flow: a senior engineer exports `proj-payments`, the file is
    reviewed and shared (repo, drive, chat), a new teammate imports it — and
    their first Claude Code session already knows the team's decisions.

Safety:
    * every node body/name passes SecretDetector.redact() at EXPORT time, so
      secrets cannot leave the machine inside memory bodies
    * import is additive and idempotent: existing node_ids are skipped, nothing
      is overwritten (supersede semantics remain intact)
    * every import writes memory_write audit rows (operation='import')

Format (JSONL):
    {"kind":"meta", "namespace":..., "exported_at":..., "nodes":N, "edges":M}
    {"kind":"node", ...row...}
    {"kind":"edge", ...row...}

Usage:
    python memory/memory_sync.py export --namespace proj-payments --out team.jsonl
    python memory/memory_sync.py import --in team.jsonl
    python memory/memory_sync.py import --in team.jsonl --namespace proj-other
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lib.db import get_db                       # noqa: E402
from audit.audit_logger import AuditLogger      # noqa: E402
from security.detectors import SecretDetector   # noqa: E402


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _redact_json(body_json: str, redact) -> str:
    """Redact secrets inside the PARSED values, not the raw escaped string —
    JSON escaping (\") would otherwise defeat the secret regexes."""
    def walk(v):
        if isinstance(v, str):
            return redact(v)
        if isinstance(v, list):
            return [walk(x) for x in v]
        if isinstance(v, dict):
            return {k: walk(x) for k, x in v.items()}
        return v
    try:
        return json.dumps(walk(json.loads(body_json)))
    except Exception:
        return redact(body_json)


def export_ns(namespace: str, out_path: Path,
              include_superseded: bool = False) -> dict:
    db = get_db()
    redact = SecretDetector(session_id="mem-sync").redact
    sup = "" if include_superseded else "AND superseded_by IS NULL"
    nodes = db.query(
        f"SELECT * FROM memory_nodes WHERE namespace=? {sup}", (namespace,))
    node_ids = {n["node_id"] for n in nodes}
    edges = [e for e in db.query(
        "SELECT * FROM memory_edges WHERE namespace=?", (namespace,))
        if e["src"] in node_ids and e["dst"] in node_ids]

    with out_path.open("w") as fh:
        fh.write(json.dumps({"kind": "meta", "namespace": namespace,
                             "exported_at": _now(), "nodes": len(nodes),
                             "edges": len(edges)}) + "\n")
        for n in nodes:
            n = dict(n)
            n.pop("embedding", None)   # embeddings are model-specific; re-derive locally
            n["name"] = redact(n["name"])
            n["body_json"] = _redact_json(n["body_json"], redact)
            fh.write(json.dumps({"kind": "node", **n}, default=str) + "\n")
        for e in edges:
            fh.write(json.dumps({"kind": "edge", **dict(e)}, default=str) + "\n")

    AuditLogger("mem-sync", actor="memory-sync").memory_write(
        namespace, "export", None, f"export:{len(nodes)}n/{len(edges)}e")
    return {"nodes": len(nodes), "edges": len(edges), "path": str(out_path)}


def import_ns(in_path: Path, namespace_override: str | None = None) -> dict:
    db = get_db()
    audit = AuditLogger("mem-sync", actor="memory-sync")
    stats = {"nodes_imported": 0, "nodes_skipped": 0,
             "edges_imported": 0, "edges_skipped": 0}
    node_cols = ("node_id", "namespace", "memory_type", "node_kind", "name",
                 "body_json", "repo", "confidence", "half_life_days",
                 "created_at", "updated_at", "last_access", "access_count",
                 "superseded_by")
    edge_cols = ("edge_id", "namespace", "src", "dst", "rel", "weight", "created_at")

    with in_path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            kind = rec.get("kind")
            if kind == "meta":
                continue
            ns = namespace_override or rec.get("namespace")
            if kind == "node":
                if db.query_one("SELECT 1 FROM memory_nodes WHERE node_id=?",
                                (rec["node_id"],)):
                    stats["nodes_skipped"] += 1
                    continue
                rec["namespace"] = ns
                # imported references to nodes we don't have stay NULL-safe
                if rec.get("superseded_by") and not db.query_one(
                        "SELECT 1 FROM memory_nodes WHERE node_id=?",
                        (rec["superseded_by"],)):
                    rec["superseded_by"] = None
                db.execute(
                    f"INSERT INTO memory_nodes ({','.join(node_cols)}) "
                    f"VALUES ({','.join('?' * len(node_cols))})",
                    tuple(rec.get(c) for c in node_cols))
                audit.memory_write(ns, rec.get("memory_type", "?"),
                                   rec["node_id"], "import")
                stats["nodes_imported"] += 1
            elif kind == "edge":
                if db.query_one("SELECT 1 FROM memory_edges WHERE edge_id=?",
                                (rec["edge_id"],)):
                    stats["edges_skipped"] += 1
                    continue
                # both endpoints must exist (either pre-existing or just imported)
                if not (db.query_one("SELECT 1 FROM memory_nodes WHERE node_id=?",
                                     (rec["src"],)) and
                        db.query_one("SELECT 1 FROM memory_nodes WHERE node_id=?",
                                     (rec["dst"],))):
                    stats["edges_skipped"] += 1
                    continue
                rec["namespace"] = ns
                db.execute(
                    f"INSERT INTO memory_edges ({','.join(edge_cols)}) "
                    f"VALUES ({','.join('?' * len(edge_cols))})",
                    tuple(rec.get(c) for c in edge_cols))
                stats["edges_imported"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Export/import a memory namespace")
    sub = ap.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("export")
    ex.add_argument("--namespace", required=True)
    ex.add_argument("--out", required=True)
    ex.add_argument("--include-superseded", action="store_true")
    im = sub.add_parser("import")
    im.add_argument("--in", dest="infile", required=True)
    im.add_argument("--namespace", help="remap into a different namespace")
    args = ap.parse_args()

    if args.cmd == "export":
        r = export_ns(args.namespace, Path(args.out), args.include_superseded)
        print(f"exported {r['nodes']} nodes / {r['edges']} edges -> {r['path']}")
        print("review the file before sharing — bodies are secret-redacted, "
              "but context may still be sensitive")
    else:
        r = import_ns(Path(args.infile), args.namespace)
        print(f"imported nodes={r['nodes_imported']} (skipped {r['nodes_skipped']}) "
              f"edges={r['edges_imported']} (skipped {r['edges_skipped']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
