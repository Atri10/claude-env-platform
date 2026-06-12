#!/usr/bin/env python3
"""
claude-env :: unified recall ("what do I know about X")
File: rag/pipelines/know.py
Purpose:
    One query fanned out across every local knowledge source, fused with
    provenance:
      * memory graph   — decisions, conventions, session learnings
                         (proj-<repo> + global namespaces)
      * RAG index      — hybrid + reranked code/doc chunks
      * git history    — `git log --grep` + `git grep` line hits

    Each source degrades gracefully: no embedding model -> RAG section is
    skipped with a note; not a git repo -> git section skipped; memory always
    works (SQLite only). So the command is useful from the first day, before
    any model download.

Usage:
    python rag/pipelines/know.py "jwt validation" --repo payments
    python rag/pipelines/know.py "retry policy" --repo payments --repo-root /work/payments
    python rag/pipelines/know.py "auth" --repo payments --format json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from memory.memory_retriever import MemoryRetriever   # noqa: E402


def memory_section(query: str, repo: str, top_k: int = 8) -> list[dict]:
    mr = MemoryRetriever(namespace=f"proj-{repo}", session_id="know",
                         actor="know", isolated=False)
    hits = mr.recall(query, depth=2, top_k=top_k, extra_ns=["global"])
    if not hits and " " in query:
        # phrase missed: retry per-term and merge (keyword recall is LIKE-based)
        seen: dict[str, dict] = {}
        for term in query.split():
            if len(term) < 3:
                continue
            for h in mr.recall(term, depth=1, top_k=top_k, extra_ns=["global"]):
                seen.setdefault(h["node_id"], h)
        hits = list(seen.values())[:top_k]
    return [{"name": h["name"], "kind": h["node_kind"],
             "namespace": h["namespace"],
             "confidence": h["effective_confidence"],
             "updated": h["updated_at"][:10],
             "body": {k: v for k, v in (h.get("body") or {}).items()
                      if k in ("task", "why", "desc", "outcome", "decision",
                               "files_edited", "summary")}}
            for h in hits]


def rag_section(query: str, repo: str, branch: str, top_n: int = 6) -> tuple[list[dict], str]:
    """Returns (hits, note). Empty hits + note when RAG is not available."""
    try:
        from rag.pipelines.retrieve import Retriever
        r = Retriever(repo=repo, branch=branch, session_id="know", actor="know")
        hits = r.query(query, top_n=top_n)
        return ([{"file": h.get("file_path"), "lines": f"{h.get('start_line')}-{h.get('end_line')}",
                  "score": round(h.get("rerank_score") or h.get("_distance") or 0, 3),
                  "preview": (h.get("text") or "")[:160]}
                 for h in hits], "")
    except Exception as exc:
        return [], f"RAG unavailable ({str(exc)[:90]})"


def git_section(query: str, repo_root: Path, limit: int = 8) -> dict:
    if not (repo_root / ".git").exists():
        return {"note": f"{repo_root} is not a git repository"}
    out = {}
    log = subprocess.run(
        ["git", "-C", str(repo_root), "log", "--grep", query, "-i",
         f"--max-count={limit}", "--format=%h %ad %s", "--date=short"],
        capture_output=True, text=True)
    out["commits"] = log.stdout.strip().splitlines() if log.returncode == 0 else []
    grep = subprocess.run(
        ["git", "-C", str(repo_root), "grep", "-in", "--max-count=2", query],
        capture_output=True, text=True)
    out["code_lines"] = grep.stdout.strip().splitlines()[:limit] \
        if grep.returncode == 0 else []
    return out


def know(query: str, repo: str, branch: str, repo_root: Path | None) -> dict:
    result = {"query": query, "repo": repo,
              "memory": memory_section(query, repo)}
    rag_hits, rag_note = rag_section(query, repo, branch)
    result["rag"] = rag_hits
    if rag_note:
        result["rag_note"] = rag_note
    if repo_root:
        result["git"] = git_section(query, repo_root)
    return result


def _print_text(r: dict) -> None:
    print(f"# what claude-env knows about: {r['query']!r}  (repo: {r['repo']})\n")
    print(f"## memory graph ({len(r['memory'])})")
    for m in r["memory"]:
        print(f"  [{m['kind']:12s}] ({m['confidence']:.2f}, {m['updated']}) {m['name']}")
        for k, v in (m["body"] or {}).items():
            print(f"      {k}: {str(v)[:120]}")
    if not r["memory"]:
        print("  (nothing yet — memory fills as sessions are ingested)")
    print(f"\n## code index ({len(r['rag'])})"
          + (f"  — {r['rag_note']}" if r.get("rag_note") else ""))
    for h in r["rag"]:
        print(f"  [{h['score']:>6.3f}] {h['file']}:{h['lines']}  {h['preview'][:90]!r}")
    if "git" in r:
        g = r["git"]
        if g.get("note"):
            print(f"\n## git — {g['note']}")
        else:
            print(f"\n## git commits mentioning it ({len(g.get('commits', []))})")
            for c in g.get("commits", []):
                print(f"  {c}")
            print(f"## git code lines ({len(g.get('code_lines', []))})")
            for line in g.get("code_lines", []):
                print(f"  {line[:140]}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Unified local recall with provenance")
    ap.add_argument("query")
    ap.add_argument("--repo", required=True)
    ap.add_argument("--branch", default="main")
    ap.add_argument("--repo-root", help="path to the working tree for git search")
    ap.add_argument("--format", choices=["text", "json"], default="text")
    args = ap.parse_args()

    root = Path(args.repo_root).resolve() if args.repo_root else None
    r = know(args.query, args.repo, args.branch, root)
    if args.format == "json":
        print(json.dumps(r, indent=2, default=str))
    else:
        _print_text(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
